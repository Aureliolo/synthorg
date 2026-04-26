"""Unit tests for envelope/helper utilities in ``handlers.common``.

These cover ``ok``, ``err``, ``require_arg``, ``require_destructive_guardrails``,
``dump_many``, ``paginate_sequence`` and ``PaginationMeta`` -- the glue every
real MCP handler uses.  The destructive-guardrail and actor-enforcement
tests live here because the helper is the single source of truth for
the invariant; per-domain handler tests exercise the same helper at the
seam they care about, but the helper itself is one file.
"""

import json
from typing import Any

import pytest
from pydantic import BaseModel

from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import require_arg

pytestmark = pytest.mark.unit


class _Thing(BaseModel):
    """Sample Pydantic model for ``dump_many`` testing."""

    id: str
    n: int


class TestOk:
    """Tests for ``ok`` envelope."""

    def test_bare_ok_includes_null_data(self) -> None:
        """``ok()`` emits ``data: null`` for a stable wire shape."""
        body = json.loads(ok())
        assert body == {"status": "ok", "data": None}

    def test_ok_with_data_wraps_payload(self) -> None:
        body = json.loads(ok(data={"a": 1}))
        assert body["status"] == "ok"
        assert body["data"] == {"a": 1}
        assert "pagination" not in body

    def test_ok_with_list_data(self) -> None:
        body = json.loads(ok(data=[1, 2, 3]))
        assert body["data"] == [1, 2, 3]

    def test_ok_includes_pagination_when_provided(self) -> None:
        meta = PaginationMeta(total=100, offset=10, limit=50)
        body = json.loads(ok(data=[], pagination=meta))
        assert body["pagination"] == {"total": 100, "offset": 10, "limit": 50}

    def test_ok_is_valid_json(self) -> None:
        # Handler return type is ``str`` by contract -- regression guard.
        assert isinstance(ok(data={"x": 1}), str)


class TestErr:
    """Tests for ``err`` envelope."""

    def test_err_includes_exception_type_and_safe_message(self) -> None:
        body = json.loads(err(ValueError("boom")))
        assert body["status"] == "error"
        assert body["error_type"] == "ValueError"
        assert isinstance(body["message"], str)
        assert "boom" in body["message"]

    def test_err_uses_domain_code_from_exception_when_present(self) -> None:
        body = json.loads(err(ArgumentValidationError("x", "string")))
        assert body["domain_code"] == "invalid_argument"

    def test_err_explicit_domain_code_overrides_exception_attr(self) -> None:
        body = json.loads(
            err(
                ArgumentValidationError("x", "string"),
                domain_code="custom_code",
            ),
        )
        assert body["domain_code"] == "custom_code"

    def test_err_without_domain_code_omits_field(self) -> None:
        body = json.loads(err(RuntimeError("oops")))
        assert "domain_code" not in body

    def test_err_message_never_leaks_secret_format(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``err`` must funnel every message through
        # ``safe_error_description`` so a future refactor that forgets the
        # sanitizer cannot leak raw ``str(exc)`` payloads.  Replace the
        # sanitizer with a unique sentinel and assert exact equality so
        # the test fails loudly on regression.
        sentinel = "__SANITIZER_SENTINEL__"
        monkeypatch.setattr(
            "synthorg.meta.mcp.handlers.common.safe_error_description",
            lambda _exc: sentinel,
        )
        body = json.loads(err(ValueError("secret=abc123 token=xyz")))
        assert body["message"] == sentinel


class TestRequireArg:
    """Tests for typed required-argument extraction."""

    def test_returns_value_when_type_matches(self) -> None:
        assert require_arg({"x": "foo"}, "x", str) == "foo"

    def test_raises_when_missing(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            require_arg({}, "x", str)
        assert ei.value.argument == "x"

    def test_raises_when_wrong_type(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_arg({"x": 42}, "x", str)

    def test_raises_when_none(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_arg({"x": None}, "x", str)

    def test_accepts_int_subtype_exactly(self) -> None:
        assert require_arg({"x": 7}, "x", int) == 7

    def test_rejects_bool_when_int_expected(self) -> None:
        # bool is an int subclass in Python; the helper must reject it
        # so ``confirm: true`` is never accepted where an int is wanted.
        with pytest.raises(ArgumentValidationError):
            require_arg({"x": True}, "x", int)


class _StubActor:
    """Minimal actor-like object carrying an audit-usable identifier.

    The destructive-op guardrail rejects actors without a stable
    identifier so audit trails always have real attribution; tests use
    this stub to satisfy that check without pulling in the full
    ``AgentIdentity`` model.
    """

    def __init__(self, *, name: str = "test-actor") -> None:
        self.name = name
        self.id = None  # id missing -- name fallback exercised


class TestGuardrails:
    """Tests for destructive-op guardrail enforcement."""

    _ACTOR = _StubActor()

    def test_returns_reason_and_actor_on_success(self) -> None:
        reason, actor = require_destructive_guardrails(
            {"confirm": True, "reason": "approved"},
            self._ACTOR,
        )
        assert reason == "approved"
        assert actor is self._ACTOR

    def test_rejects_missing_actor(self) -> None:
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": True, "reason": "x"},
                None,
            )
        assert ei.value.violation == "missing_actor"

    def test_rejects_confirm_false(self) -> None:
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": False, "reason": "x"},
                self._ACTOR,
            )
        assert ei.value.violation == "missing_confirm"

    def test_rejects_missing_confirm(self) -> None:
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails({"reason": "x"}, self._ACTOR)
        assert ei.value.violation == "missing_confirm"

    def test_rejects_blank_reason(self) -> None:
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": True, "reason": "   "},
                self._ACTOR,
            )
        assert ei.value.violation == "missing_reason"

    def test_rejects_missing_reason(self) -> None:
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": True},
                self._ACTOR,
            )
        assert ei.value.violation == "missing_reason"

    def test_rejects_non_bool_confirm(self) -> None:
        # ``confirm: "true"`` would slip past a naive ``if confirm:``
        # check; the helper must insist on a real bool.
        with pytest.raises(GuardrailViolationError):
            require_destructive_guardrails(
                {"confirm": "true", "reason": "x"},
                self._ACTOR,
            )

    def test_rejects_unattributable_actor(self) -> None:
        # Opaque actors without ``.id`` or a non-blank ``.name`` produce
        # destructive-op audit entries with no real attribution; the
        # guardrail must reject them the same way it rejects ``None``.
        opaque = object()
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": True, "reason": "approved"},
                opaque,
            )
        assert ei.value.violation == "missing_actor"

    def test_rejects_blank_name_actor(self) -> None:
        # A ``name`` attribute of whitespace is not a usable identifier.
        blank = _StubActor(name="   ")
        with pytest.raises(GuardrailViolationError) as ei:
            require_destructive_guardrails(
                {"confirm": True, "reason": "approved"},
                blank,
            )
        assert ei.value.violation == "missing_actor"

    def test_accepts_actor_with_id_only(self) -> None:
        # An actor with ``.id`` set but no ``.name`` still attributes the
        # destructive op.
        class _IdOnly:
            id = "actor-uuid-1234"
            name = None

        reason, actor = require_destructive_guardrails(
            {"confirm": True, "reason": "approved"},
            _IdOnly(),
        )
        assert reason == "approved"
        assert actor.id == "actor-uuid-1234"


class TestDumpMany:
    """Tests for the batch model-dump helper."""

    def test_dumps_each_model_in_json_mode(self) -> None:
        items = (_Thing(id="a", n=1), _Thing(id="b", n=2))
        assert dump_many(items) == [{"id": "a", "n": 1}, {"id": "b", "n": 2}]

    def test_empty_iterable_returns_empty_list(self) -> None:
        assert dump_many([]) == []

    def test_accepts_generator(self) -> None:
        def _gen() -> Any:
            yield _Thing(id="a", n=1)

        assert dump_many(_gen()) == [{"id": "a", "n": 1}]


class TestPaginateSequence:
    """Tests for in-memory pagination helper."""

    def test_first_page(self) -> None:
        page, meta = paginate_sequence(
            list(range(100)),
            offset=0,
            limit=10,
        )
        assert page == list(range(10))
        assert meta == PaginationMeta(total=100, offset=0, limit=10)

    def test_middle_page(self) -> None:
        page, meta = paginate_sequence(
            list(range(100)),
            offset=20,
            limit=10,
        )
        assert page == list(range(20, 30))
        assert meta.total == 100

    def test_offset_past_end_returns_empty_page(self) -> None:
        page, meta = paginate_sequence(
            list(range(5)),
            offset=50,
            limit=10,
        )
        assert page == []
        assert meta.total == 5

    def test_explicit_total_overrides_sequence_length(self) -> None:
        # For services that already know the unfiltered total (e.g.
        # ``TaskEngine.list_tasks`` returns ``(items, total)``).
        page, meta = paginate_sequence(
            [1, 2, 3],
            offset=0,
            limit=10,
            total=500,
        )
        assert page == [1, 2, 3]
        assert meta.total == 500

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(ArgumentValidationError):
            paginate_sequence([1, 2], offset=-1, limit=10)

    def test_rejects_non_positive_limit(self) -> None:
        with pytest.raises(ArgumentValidationError):
            paginate_sequence([1, 2], offset=0, limit=0)
