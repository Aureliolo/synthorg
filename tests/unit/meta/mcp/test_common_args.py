"""Unit tests for the centralized MCP handler argument helpers.

These cover the six new helpers in ``handlers.common_args`` that
absorb the per-domain duplicates: ``require_actor_id``, ``actor_label``,
``get_optional_str``, ``require_dict``, ``parse_time_window``, and
``parse_str_sequence``. The existing helpers (``require_arg``,
``require_non_blank``, ``actor_id``, ``coerce_pagination``) keep their
coverage in ``test_common_envelope.py`` -- this file pins the new
contracts only.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common_args import (
    actor_label,
    get_optional_str,
    parse_str_sequence,
    parse_time_window,
    require_actor_id,
    require_dict,
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

    def test_falls_back_to_name(self) -> None:
        assert actor_label(_Actor(id=None, name="alice")) == "alice"

    def test_default_fallback_when_no_actor(self) -> None:
        assert actor_label(None) == "mcp-anonymous"

    def test_default_fallback_when_no_identifier(self) -> None:
        assert actor_label(_Actor(id=None, name=None)) == "mcp-anonymous"

    def test_blank_name_falls_through_to_fallback(self) -> None:
        assert actor_label(_Actor(id=None, name="   ")) == "mcp-anonymous"

    def test_blank_string_id_falls_through_to_name(self) -> None:
        # A whitespace-only string ``id`` is not a usable identifier; the
        # helper must fall through to ``name``. organization.py's variant
        # already enforced this; consolidation preserves it.
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

    def test_returns_none_when_missing(self) -> None:
        assert get_optional_str({}, "k") is None

    def test_returns_none_when_value_is_none(self) -> None:
        assert get_optional_str({"k": None}, "k") is None

    def test_returns_none_when_empty_string(self) -> None:
        assert get_optional_str({"k": ""}, "k") is None

    def test_raises_on_whitespace_only(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": "   "}, "k")

    def test_raises_on_non_string(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": 42}, "k")

    def test_raises_on_dict(self) -> None:
        with pytest.raises(ArgumentValidationError):
            get_optional_str({"k": {"nested": "x"}}, "k")


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

    def test_deep_copy_default_decouples_from_input(self) -> None:
        original: dict[str, Any] = {"k": {"nested": [1, 2]}}
        result = require_dict(original, "k")
        # Mutating the returned dict must not affect the original input.
        result["nested"].append(3)
        assert original["k"]["nested"] == [1, 2]

    def test_deep_copy_disabled_returns_shallow_copy(self) -> None:
        original: dict[str, Any] = {"k": {"nested": [1, 2]}}
        result = require_dict(original, "k", deep_copy=False)
        # The outer dict is a copy (the implementation uses dict(raw)),
        # but nested mutables are shared.
        assert result is not original["k"]
        assert result["nested"] is original["k"]["nested"]


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

    def test_raises_on_missing_until_when_required(self) -> None:
        with pytest.raises(ArgumentValidationError) as ei:
            parse_time_window({"since": self._SINCE})
        assert ei.value.argument == "until"

    def test_missing_until_defaults_to_now_when_optional(self) -> None:
        fixed = datetime(2026, 5, 1, tzinfo=UTC)
        with patch(
            "synthorg.meta.mcp.handlers.common_args.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.fromisoformat = datetime.fromisoformat
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

    def test_raises_when_since_not_earlier_than_until(self) -> None:
        with pytest.raises(ArgumentValidationError):
            parse_time_window(
                {"since": self._UNTIL, "until": self._SINCE},
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

    def test_returns_none_when_missing(self) -> None:
        assert parse_str_sequence({}, "k") is None

    def test_returns_none_when_value_is_none(self) -> None:
        assert parse_str_sequence({"k": None}, "k") is None

    def test_returns_none_when_value_is_empty_string(self) -> None:
        assert parse_str_sequence({"k": ""}, "k") is None

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
        # parse (returns ``()``); only ``None`` / ``""`` map to None.
        # Callers that need non-empty enforce that themselves.
        result = parse_str_sequence({"k": []}, "k")
        assert result == ()
