"""Unit tests for the centralized MCP handler argument helpers.

Covers every public helper in
:mod:`synthorg.meta.mcp.handlers.common_args`. Tests are grouped per
helper:

* New in the centralization refactor: ``require_actor_id``,
  ``actor_label``, ``get_optional_str``, ``require_dict``,
  ``parse_time_window``, ``parse_str_sequence``.
* Moved verbatim from the original ``common.py`` and now re-tested
  here so the new module owns its own coverage net: ``require_arg``,
  ``require_non_blank``, ``actor_id``, ``coerce_pagination``.

``test_common_envelope.py`` continues to cover the envelope helpers
that stayed in ``common.py`` (``ok``, ``err``, ``paginate_sequence``,
``require_destructive_guardrails``, etc.).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    actor_label,
    coerce_pagination,
    get_optional_str,
    parse_str_sequence,
    parse_time_window,
    require_actor_id,
    require_arg,
    require_dict,
    require_non_blank,
)

pytestmark = pytest.mark.unit


class _Actor:
    """Minimal actor stub.

    AgentIdentity is a Pydantic model with required fields; tests don't
    need its full surface so we use a plain stub with the two attributes
    the helpers care about.
    """

    def __init__(self, *, id: Any = None, name: Any = None) -> None:  # noqa: A002
        self.id = id
        self.name = name


class TestRequireActorId:
    """``require_actor_id`` is the raising counterpart of ``actor_id``."""

    def test_returns_id_when_present(self) -> None:
        assert require_actor_id(_Actor(id="agent-1")) == "agent-1"

    def test_coerces_uuid_id_via_str(self) -> None:
        uid = UUID("12345678-1234-5678-1234-567812345678")
        assert require_actor_id(_Actor(id=uid)) == str(uid)

    def test_falls_back_to_name(self) -> None:
        assert require_actor_id(_Actor(id=None, name="alice")) == "alice"

    def test_raises_on_none_actor(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            require_actor_id(None)
        assert ei.value.argument == "actor"

    def test_raises_when_no_identifier_available(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_actor_id(_Actor(id=None, name=None))

    def test_raises_when_name_is_blank(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_actor_id(_Actor(id=None, name="   "))


class TestActorLabel:
    """``actor_label`` returns a guaranteed-non-blank attribution string."""

    def test_returns_id_when_present(self) -> None:
        assert actor_label(_Actor(id="agent-1")) == "agent-1"

    def test_coerces_uuid_id_via_str(self) -> None:
        uid = UUID("12345678-1234-5678-1234-567812345678")
        assert actor_label(_Actor(id=uid)) == str(uid)

    def test_coerces_int_id_via_str(self) -> None:
        # Non-string non-UUID ids (e.g. integer surrogate keys) must be
        # string-coerced rather than rejected.
        assert actor_label(_Actor(id=12345)) == "12345"

    def test_falls_back_to_name(self) -> None:
        assert actor_label(_Actor(id=None, name="alice")) == "alice"

    def test_default_fallback_when_no_actor(self) -> None:
        assert actor_label(None) == "mcp-anonymous"

    def test_default_fallback_when_no_identifier(self) -> None:
        assert actor_label(_Actor(id=None, name=None)) == "mcp-anonymous"

    def test_blank_name_falls_through_to_fallback(self) -> None:
        assert actor_label(_Actor(id=None, name="   ")) == "mcp-anonymous"

    def test_blank_string_id_with_none_name_returns_fallback(self) -> None:
        # Blank string id and absent name -> neither is usable; fall back.
        # A whitespace-only string id must not be returned as an audit
        # identifier; doing so would create unhelpful audit trails with
        # "   " as the actor.
        assert actor_label(_Actor(id="   ", name=None)) == "mcp-anonymous"

    def test_blank_string_id_falls_through_to_name(self) -> None:
        # A whitespace-only string id is not a usable identifier; the
        # helper falls through to the next resolution step (``name``).
        # organization.py's variant already enforced this; consolidation
        # preserves it. Without this rule, audit logs would record
        # whitespace-only attribution strings.
        assert actor_label(_Actor(id="   ", name="alice")) == "alice"

    def test_custom_fallback_respected(self) -> None:
        assert actor_label(None, fallback="anon-x") == "anon-x"

    def test_returns_str_subtype_compatible(self) -> None:
        # NotBlankStr is Annotated[str, ...]: at runtime it's plain str,
        # so equality / isinstance checks work transparently.
        result = actor_label(_Actor(id="agent-1"))
        assert isinstance(result, str)


class TestGetOptionalStr:
    """``get_optional_str`` returns optional non-blank strings."""

    def test_returns_string_when_present(self) -> None:
        assert get_optional_str({"k": "value"}, "k") == "value"

    @pytest.mark.parametrize(
        ("arguments", "label"),
        [
            ({}, "missing key"),
            ({"k": None}, "explicit None"),
            ({"k": ""}, "empty string"),
        ],
    )
    def test_returns_none_for_absent_or_empty(
        self,
        arguments: dict[str, Any],
        label: str,
    ) -> None:
        assert get_optional_str(arguments, "k") is None

    def test_raises_on_whitespace_only(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": "   "}, "k")

    def test_raises_on_non_string(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": 42}, "k")

    def test_raises_on_dict(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": {"nested": "x"}}, "k")

    def test_returned_value_preserves_whitespace(self) -> None:
        # Docstring documents that the returned value is NOT stripped:
        # validation uses the stripped form, but the original is
        # preserved verbatim. Pin that contract here so a future
        # refactor doesn't silently start stripping at this seam.
        assert get_optional_str({"k": "  hello  "}, "k") == "  hello  "


class TestRequireDict:
    """``require_dict`` validates dict args; supports value-type + deep-copy."""

    def test_returns_dict_when_no_value_type(self) -> None:
        result = require_dict({"k": {"a": 1, "b": "x"}}, "k")
        assert result == {"a": 1, "b": "x"}

    def test_raises_when_missing(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_dict({}, "k")

    def test_raises_when_not_dict(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_dict({"k": [("a", 1)]}, "k")

    def test_raises_on_non_string_key(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_dict({"k": {1: "v"}}, "k")

    def test_value_type_str_rejects_non_str_values(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_dict({"k": {"a": 1}}, "k", value_type=str)

    def test_value_type_str_accepts_str_values(self) -> None:
        result = require_dict({"k": {"a": "1"}}, "k", value_type=str)
        assert result == {"a": "1"}

    def test_value_type_none_accepts_none_values(self) -> None:
        # With no ``value_type`` constraint, ``None`` values are accepted
        # verbatim. Pin the contract so a future refactor doesn't quietly
        # start filtering or rejecting them.
        result = require_dict({"k": {"a": None, "b": 1}}, "k")
        assert result == {"a": None, "b": 1}

    def test_value_type_str_rejects_none_values(self) -> None:
        # When ``value_type=str`` is set, ``None`` is not a string and
        # must be rejected -- isinstance(None, str) is False.
        with pytest.raises(ArgumentValidationError):
            require_dict({"k": {"a": None}}, "k", value_type=str)

    def test_deep_copy_default_decouples_from_input(self) -> None:
        original: dict[str, Any] = {"k": {"nested": [1, 2]}}
        result = require_dict(original, "k")
        # Mutating the returned dict's nested mutables must not affect
        # the original input.
        result["nested"].append(3)
        assert original["k"]["nested"] == [1, 2]

    def test_deep_copy_disabled_shares_nested_mutables(self) -> None:
        # Behavior contract: with ``deep_copy=False`` the outer dict is
        # a fresh copy (mutating ``result`` keys doesn't affect the
        # input) but nested mutables are shared (mutating a nested list
        # DOES leak through to the input). Asserting on observable
        # behavior rather than ``is`` identity, so a future refactor
        # that swaps ``dict(raw)`` for ``dict.copy()`` or similar still
        # passes if the contract holds.
        original: dict[str, Any] = {"k": {"nested": [1, 2]}}
        result = require_dict(original, "k", deep_copy=False)
        # Mutating outer-level keys: must not leak into the original.
        result["new_key"] = "x"
        assert "new_key" not in original["k"]
        # Mutating nested mutable: DOES leak (shared reference).
        result["nested"].append(99)
        assert original["k"]["nested"] == [1, 2, 99]


class TestParseTimeWindow:
    """``parse_time_window`` parses since/until ISO 8601 datetime args."""

    _SINCE = "2026-04-01T00:00:00+00:00"
    _UNTIL = "2026-04-02T00:00:00+00:00"

    def test_returns_tz_aware_pair(self) -> None:
        since, until = parse_time_window(
            {"since": self._SINCE, "until": self._UNTIL},
        )
        assert since == datetime(2026, 4, 1, tzinfo=UTC)
        assert until == datetime(2026, 4, 2, tzinfo=UTC)

    def test_raises_on_missing_since(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            parse_time_window({"until": self._UNTIL})
        assert ei.value.argument == "since"

    def test_raises_on_blank_whitespace_since(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            parse_time_window({"since": "   ", "until": self._UNTIL})
        assert ei.value.argument == "since"

    def test_raises_on_missing_until_when_required(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            parse_time_window({"since": self._SINCE})
        assert ei.value.argument == "until"

    def test_missing_until_defaults_to_now_when_optional(self) -> None:
        # The implementation routes ``datetime.now(UTC)`` through the
        # private ``_now_utc`` seam so tests can patch a single mockable
        # function (the stdlib ``datetime`` class is immutable and can't
        # be patched directly). Patch only that seam; everything else
        # in the parser exercises the real code path.
        fixed = datetime(2026, 5, 1, tzinfo=UTC)
        with patch(
            "synthorg.meta.mcp.handlers.common_args._now_utc",
            return_value=fixed,
        ):
            since, until = parse_time_window(
                {"since": self._SINCE},
                until_required=False,
            )
        assert since == datetime(2026, 4, 1, tzinfo=UTC)
        assert until == fixed

    def test_raises_on_naive_since(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": "2026-04-01T00:00:00", "until": self._UNTIL},
            )

    def test_raises_on_naive_until(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": self._SINCE, "until": "2026-04-02T00:00:00"},
            )

    def test_raises_when_since_strictly_after_until(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": self._UNTIL, "until": self._SINCE},
            )

    def test_raises_when_since_equals_until(self) -> None:
        # Boundary: ``since == until`` is rejected (windows must be
        # non-empty). The implementation uses ``>=``; this test pins
        # that the equality case is part of the rejection set.
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": self._SINCE, "until": self._SINCE},
            )

    def test_raises_on_malformed_since(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": "not-a-date", "until": self._UNTIL},
            )

    def test_raises_on_non_string_until(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window({"since": self._SINCE, "until": 123})

    def test_accepts_non_utc_tz(self) -> None:
        # ``timezone-aware`` is the only requirement; arbitrary offsets
        # are valid input (services normalize internally).
        since, until = parse_time_window(
            {
                "since": "2026-04-01T00:00:00+02:00",
                "until": "2026-04-02T00:00:00+02:00",
            },
        )
        assert since.utcoffset() == timedelta(hours=2)
        assert until.utcoffset() == timedelta(hours=2)


class TestParseStrSequence:
    """``parse_str_sequence`` parses optional sequence-of-non-blank-strings."""

    def test_returns_tuple_when_present(self) -> None:
        result = parse_str_sequence({"k": ["a", "b", "c"]}, "k")
        assert result == ("a", "b", "c")

    def test_accepts_tuple_input(self) -> None:
        result = parse_str_sequence({"k": ("a", "b")}, "k")
        assert result == ("a", "b")

    @pytest.mark.parametrize(
        ("arguments", "label"),
        [
            ({}, "missing key"),
            ({"k": None}, "explicit None"),
            ({"k": ""}, "empty string"),
        ],
    )
    def test_returns_none_for_absent_or_empty(
        self,
        arguments: dict[str, Any],
        label: str,
    ) -> None:
        assert parse_str_sequence(arguments, "k") is None

    def test_raises_when_not_list_or_tuple(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_str_sequence({"k": "not-a-list"}, "k")

    def test_raises_when_dict(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_str_sequence({"k": {"a": 1}}, "k")

    def test_raises_on_non_string_entry(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_str_sequence({"k": ["a", 42]}, "k")

    def test_raises_on_blank_entry(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_str_sequence({"k": ["a", "   "]}, "k")

    def test_accepts_empty_list(self) -> None:
        # Existing behavior in analytics.py: an empty list is a valid
        # parse and returns ``()``; only ``None`` / ``""`` map to None.
        # Non-empty validation is the caller's domain-specific rule and
        # belongs in the caller, not in this generic helper.
        result = parse_str_sequence({"k": []}, "k")
        assert result == ()


class TestCoercePagination:
    """``coerce_pagination`` parses offset/limit args with strict bounds.

    Moved from ``common.py`` to ``common_args.py`` during the
    centralization refactor; previously covered only via ``paginate_sequence``
    integration tests in ``test_common_envelope.py``. These tests pin
    the helper's contract directly.
    """

    def test_returns_defaults_when_missing(self) -> None:
        assert coerce_pagination({}) == (0, 50)

    def test_returns_defaults_when_empty_strings(self) -> None:
        assert coerce_pagination({"offset": "", "limit": ""}) == (0, 50)

    def test_default_limit_override(self) -> None:
        assert coerce_pagination({}, default_limit=200) == (0, 200)

    def test_returns_explicit_values(self) -> None:
        assert coerce_pagination({"offset": 10, "limit": 25}) == (10, 25)

    def test_coerces_numeric_strings(self) -> None:
        # JSON-RPC payloads can deliver pagination args as strings;
        # the helper coerces via ``int()`` after rejecting bools.
        assert coerce_pagination({"offset": "5", "limit": "50"}) == (5, 50)

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            coerce_pagination({"offset": -1})
        assert ei.value.argument == "offset"

    def test_rejects_zero_limit(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            coerce_pagination({"limit": 0})
        assert ei.value.argument == "limit"

    def test_rejects_negative_limit(self) -> None:
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({"limit": -5})

    def test_rejects_bool_offset(self) -> None:
        # ``isinstance(True, int)`` is True in Python; rejecting bools
        # explicitly prevents ``confirm: true`` accidentally satisfying
        # an int field.
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({"offset": True})

    def test_rejects_bool_limit(self) -> None:
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({"limit": True})

    def test_rejects_non_numeric_string(self) -> None:
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({"offset": "abc"})

    def test_rejects_dict_value(self) -> None:
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({"limit": {"a": 1}})

    def test_rejects_invalid_default_limit(self) -> None:
        # The ``default_limit`` is validated through the same gate so a
        # caller passing ``default_limit=0`` cannot smuggle an invalid
        # default past the bound check.
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({}, default_limit=0)

    def test_rejects_bool_default_limit(self) -> None:
        with pytest.raises(ArgumentValidationError):
            coerce_pagination({}, default_limit=True)


class TestRequireArg:
    """``require_arg`` extracts a typed required argument or raises.

    Moved from ``common.py`` to ``common_args.py`` during the
    centralization refactor. Tests live here so the module owns its
    own coverage; ``test_common_envelope.py`` still exercises the
    helper indirectly via envelope tests.
    """

    def test_returns_value_when_type_matches(self) -> None:
        assert require_arg({"k": "v"}, "k", str) == "v"

    def test_raises_when_missing(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            require_arg({}, "k", str)
        assert ei.value.argument == "k"

    def test_raises_when_value_is_none(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_arg({"k": None}, "k", str)

    def test_raises_on_wrong_type(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_arg({"k": 42}, "k", str)

    def test_accepts_int_when_int_expected(self) -> None:
        assert require_arg({"k": 7}, "k", int) == 7

    def test_rejects_bool_when_int_expected(self) -> None:
        # ``isinstance(True, int)`` is True in Python; the helper must
        # reject bools so a ``confirm: true`` payload never satisfies
        # an int field.
        with pytest.raises(ArgumentValidationError):
            require_arg({"k": True}, "k", int)


class TestRequireNonBlank:
    """``require_non_blank`` extracts a non-blank stripped string.

    Moved from ``common.py`` to ``common_args.py`` during the
    centralization refactor.
    """

    def test_returns_stripped_value(self) -> None:
        assert require_non_blank({"k": "  value  "}, "k") == "value"

    def test_returns_value_when_already_clean(self) -> None:
        assert require_non_blank({"k": "value"}, "k") == "value"

    def test_raises_when_missing(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            require_non_blank({}, "k")
        assert ei.value.argument == "k"

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_non_blank({"k": ""}, "k")

    def test_raises_on_whitespace_only(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_non_blank({"k": "   "}, "k")

    def test_raises_on_non_string(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_non_blank({"k": 42}, "k")

    def test_raises_on_none(self) -> None:
        with pytest.raises(ArgumentValidationError):
            require_non_blank({"k": None}, "k")


class TestActorId:
    """``actor_id`` returns a stable audit identifier or ``None``.

    Moved from ``common.py`` to ``common_args.py`` during the
    centralization refactor. ``require_actor_id`` is the raising
    counterpart and has its own test class above.
    """

    def test_returns_id_when_present(self) -> None:
        assert actor_id(_Actor(id="agent-1")) == "agent-1"

    def test_coerces_uuid_id_via_str(self) -> None:
        uid = UUID("12345678-1234-5678-1234-567812345678")
        assert actor_id(_Actor(id=uid)) == str(uid)

    def test_falls_back_to_name(self) -> None:
        assert actor_id(_Actor(id=None, name="alice")) == "alice"

    def test_strips_whitespace_from_name(self) -> None:
        assert actor_id(_Actor(id=None, name="  alice  ")) == "alice"

    def test_returns_none_for_none_actor(self) -> None:
        assert actor_id(None) is None

    def test_returns_none_for_blank_name(self) -> None:
        assert actor_id(_Actor(id=None, name="   ")) is None

    def test_returns_none_for_no_identifier(self) -> None:
        assert actor_id(_Actor(id=None, name=None)) is None

    def test_returns_none_for_non_string_name(self) -> None:
        assert actor_id(_Actor(id=None, name=42)) is None
