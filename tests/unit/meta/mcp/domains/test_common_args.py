"""Tests for the shared MCP args helpers in ``_common_args.py``."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.meta.mcp.domains._common_args import IsoDatetimeStr


class _TimeWindowArgs(BaseModel):
    """Minimal model exercising :data:`IsoDatetimeStr` validation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    since: IsoDatetimeStr | None = None
    until: IsoDatetimeStr | None = None


@pytest.mark.unit
class TestIsoDatetimeStr:
    @pytest.mark.parametrize(
        "value",
        [
            "2026-04-29T22:50:23+00:00",
            "2026-04-29T22:50:23Z",
            "2026-04-29T22:50:23.123456+00:00",
            "2026-04-29T15:50:23-07:00",
        ],
    )
    def test_valid_aware_iso_8601(self, value: str) -> None:
        """Timezone-aware ISO 8601 strings round-trip unchanged."""
        args = _TimeWindowArgs(since=value)
        assert args.since == value

    def test_none_allowed(self) -> None:
        """Optional fields accept ``None``."""
        assert _TimeWindowArgs().since is None

    @pytest.mark.parametrize(
        "value",
        [
            "tomorrow-ish",
            "2026-04-29",
            "not-a-date",
            "",
            "   ",
            "2026/04/29 22:50:23",
        ],
    )
    def test_unparseable_rejected(self, value: str) -> None:
        """Strings that don't parse as ISO 8601 fail validation."""
        with pytest.raises(ValidationError):
            _TimeWindowArgs(since=value)

    def test_naive_datetime_rejected(self) -> None:
        """Naive ISO 8601 strings (no offset / no Z) are rejected."""
        with pytest.raises(ValidationError):
            _TimeWindowArgs(since="2026-04-29T22:50:23")

    def test_rejected_input_not_echoed_in_error_message(self) -> None:
        """Validator ``msg`` MUST NOT splice the rejected raw input.

        ``MCPToolInvoker`` formats ``ValidationError`` with
        ``include_input=False`` -- only the per-error ``msg`` strings
        flow into logs and the ``invalid_argument`` envelope.  If the
        validator splices the input into ``msg``, that masking is
        defeated; this regression guard checks the ``msg`` field on
        each error directly.
        """
        secret = "tomorrow-ish-leak-token-987654321"
        with pytest.raises(ValidationError) as exc_info:
            _TimeWindowArgs(since=secret)
        for error in exc_info.value.errors(include_input=False):
            assert secret not in error["msg"]
