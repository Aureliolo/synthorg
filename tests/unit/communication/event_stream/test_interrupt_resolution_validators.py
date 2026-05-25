# mypy: disable-error-code="explicit-any"
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
    INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY,
)
from synthorg.core.registry import StrategyFactoryNotFoundError

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
        assert set(INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.names()) == {
            t.value for t in InterruptType
        }

    def test_registry_raises_for_unknown_type(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                "unregistered", _make_resolution()
            )

    def test_lookup_accepts_enum_member(self) -> None:
        assert InterruptType.TOOL_APPROVAL in INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY


# ── TOOL_APPROVAL ────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateToolApproval:
    def test_decision_set_returns_none(self) -> None:
        assert (
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                InterruptType.TOOL_APPROVAL,
                _make_resolution(decision=ResumeDecision.APPROVE),
            )
            is None
        )

    def test_decision_missing_returns_note(self) -> None:
        resolution = _make_resolution(decision=None, response="some text")
        assert (
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                InterruptType.TOOL_APPROVAL, resolution
            )
            == "TOOL_APPROVAL requires decision"
        )


# ── INFO_REQUEST ─────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateInfoRequest:
    def test_response_set_returns_none(self) -> None:
        resolution = _make_resolution(decision=None, response="My answer")
        assert (
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                InterruptType.INFO_REQUEST, resolution
            )
            is None
        )

    def test_response_missing_returns_note(self) -> None:
        resolution = _make_resolution(
            decision=ResumeDecision.APPROVE,
            response=None,
        )
        assert (
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                InterruptType.INFO_REQUEST, resolution
            )
            == "INFO_REQUEST requires response"
        )
