"""Unit tests for the InterruptType resolution-validator registry."""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.communication.event_stream.interrupt import (
    InterruptResolution,
    InterruptType,
    ResumeDecision,
)
from synthorg.communication.event_stream.interrupt_resolution_validators import (
    INTERRUPT_RESOLUTION_VALIDATORS,
)

_TS = datetime(2026, 5, 10, tzinfo=UTC)


def _make_resolution(**overrides: Any) -> InterruptResolution:
    defaults: dict[str, Any] = {
        "interrupt_id": "int-001",
        "decision": ResumeDecision.APPROVE,
        "resolved_at": _TS,
        "resolved_by": "operator",
    }
    defaults.update(overrides)
    return InterruptResolution(**defaults)


# ── Registry exhaustiveness ──────────────────────────────────────


@pytest.mark.unit
class TestRegistryShape:
    def test_registry_covers_every_interrupt_type(self) -> None:
        assert set(INTERRUPT_RESOLUTION_VALIDATORS.keys()) == set(InterruptType)

    def test_registry_raises_keyerror_for_unknown_type(self) -> None:
        with pytest.raises(KeyError):
            _ = INTERRUPT_RESOLUTION_VALIDATORS[object()]  # type: ignore[index]


# ── TOOL_APPROVAL ────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateToolApproval:
    def test_decision_set_returns_none(self) -> None:
        validator = INTERRUPT_RESOLUTION_VALIDATORS[InterruptType.TOOL_APPROVAL]
        assert validator(_make_resolution(decision=ResumeDecision.APPROVE)) is None

    def test_decision_missing_returns_legacy_note(self) -> None:
        validator = INTERRUPT_RESOLUTION_VALIDATORS[InterruptType.TOOL_APPROVAL]
        resolution = _make_resolution(decision=None, response="some text")
        assert validator(resolution) == "TOOL_APPROVAL requires decision"


# ── INFO_REQUEST ─────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateInfoRequest:
    def test_response_set_returns_none(self) -> None:
        validator = INTERRUPT_RESOLUTION_VALIDATORS[InterruptType.INFO_REQUEST]
        resolution = _make_resolution(decision=None, response="My answer")
        assert validator(resolution) is None

    def test_response_missing_returns_legacy_note(self) -> None:
        validator = INTERRUPT_RESOLUTION_VALIDATORS[InterruptType.INFO_REQUEST]
        resolution = _make_resolution(
            decision=ResumeDecision.APPROVE,
            response=None,
        )
        assert validator(resolution) == "INFO_REQUEST requires response"
