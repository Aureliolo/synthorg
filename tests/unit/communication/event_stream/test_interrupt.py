# mypy: disable-error-code="explicit-any"
"""Tests for interrupt models."""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptResolution,
    InterruptType,
    ResumeDecision,
)


@pytest.mark.unit
class TestInterruptType:
    def test_tool_approval_value(self) -> None:
        assert InterruptType.TOOL_APPROVAL.value == "tool_approval"

    def test_info_request_value(self) -> None:
        assert InterruptType.INFO_REQUEST.value == "info_request"

    def test_member_count(self) -> None:
        assert len(InterruptType) == 2


@pytest.mark.unit
class TestResumeDecision:
    def test_approve_value(self) -> None:
        assert ResumeDecision.APPROVE.value == "approve"

    def test_reject_value(self) -> None:
        assert ResumeDecision.REJECT.value == "reject"

    def test_revise_value(self) -> None:
        assert ResumeDecision.REVISE.value == "revise"

    def test_member_count(self) -> None:
        assert len(ResumeDecision) == 3


def _make_interrupt(**overrides: Any) -> Interrupt:
    defaults: dict[str, Any] = {
        "id": "int-001",
        "type": InterruptType.TOOL_APPROVAL,
        "session_id": "session-abc",
        "agent_id": "agent-eng-001",
        "created_at": datetime(2026, 4, 13, tzinfo=UTC),
        "timeout_seconds": 300.0,
        "tool_name": "deploy_service",
    }
    defaults.update(overrides)
    return Interrupt(**defaults)


def _make_info_interrupt(**overrides: Any) -> Interrupt:
    defaults: dict[str, Any] = {
        "id": "int-002",
        "type": InterruptType.INFO_REQUEST,
        "session_id": "session-abc",
        "agent_id": "agent-eng-001",
        "created_at": datetime(2026, 4, 13, tzinfo=UTC),
        "timeout_seconds": 600.0,
        "question": "Which database should I target?",
    }
    defaults.update(overrides)
    return Interrupt(**defaults)


@pytest.mark.unit
class TestInterrupt:
    def test_tool_approval_construction(self) -> None:
        interrupt = _make_interrupt()
        assert interrupt.id == "int-001"
        assert interrupt.type == InterruptType.TOOL_APPROVAL
        assert interrupt.tool_name == "deploy_service"
        assert interrupt.timeout_seconds == 300.0

    def test_info_request_construction(self) -> None:
        interrupt = _make_info_interrupt()
        assert interrupt.type == InterruptType.INFO_REQUEST
        assert interrupt.question == "Which database should I target?"

    def test_frozen(self) -> None:
        interrupt = _make_interrupt()
        with pytest.raises(Exception, match="frozen"):
            interrupt.id = "changed"  # type: ignore[misc]

    def test_tool_approval_requires_tool_name(self) -> None:
        with pytest.raises(ValueError, match="tool_name"):
            _make_interrupt(tool_name=None)

    def test_info_request_requires_question(self) -> None:
        with pytest.raises(ValueError, match="question"):
            _make_info_interrupt(question=None)

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _make_interrupt(id="")

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than"):
            _make_interrupt(timeout_seconds=0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than"):
            _make_interrupt(timeout_seconds=-1.0)

    def test_optional_evidence_package_id(self) -> None:
        interrupt = _make_interrupt(evidence_package_id="ep-001")
        assert interrupt.evidence_package_id == "ep-001"

    def test_optional_context_snippet(self) -> None:
        interrupt = _make_info_interrupt(context_snippet="some context")
        assert interrupt.context_snippet == "some context"

    def test_tool_args_deep_copied(self) -> None:
        original: dict[str, object] = {
            "key": "value",
            "nested": {"inner": "original"},
        }
        interrupt = _make_interrupt(tool_args=original)
        original["key"] = "mutated"
        original["nested"]["inner"] = "mutated"  # type: ignore[index]
        assert interrupt.tool_args is not None
        assert interrupt.tool_args["key"] == "value"
        assert interrupt.tool_args["nested"]["inner"] == "original"  # type: ignore[index]


def _make_resolution(**overrides: Any) -> InterruptResolution:
    defaults: dict[str, Any] = {
        "interrupt_id": "int-001",
        "decision": ResumeDecision.APPROVE,
        "resolved_at": datetime(2026, 4, 13, 0, 5, tzinfo=UTC),
        "resolved_by": "admin-user",
    }
    defaults.update(overrides)
    return InterruptResolution(**defaults)


@pytest.mark.unit
class TestInterruptResolution:
    def test_construction(self) -> None:
        resolution = _make_resolution()
        assert resolution.interrupt_id == "int-001"
        assert resolution.decision == ResumeDecision.APPROVE
        assert resolution.resolved_by == "admin-user"

    def test_frozen(self) -> None:
        resolution = _make_resolution()
        with pytest.raises(Exception, match="frozen"):
            resolution.interrupt_id = "changed"  # type: ignore[misc]

    def test_with_feedback(self) -> None:
        resolution = _make_resolution(feedback="Looks good, approve it.")
        assert resolution.feedback == "Looks good, approve it."

    def test_info_request_response(self) -> None:
        resolution = _make_resolution(
            decision=None,
            response="Use the staging database.",
        )
        assert resolution.response == "Use the staging database."
        assert resolution.decision is None

    def test_empty_payload_rejected(self) -> None:
        with pytest.raises(ValueError, match="decision or response is required"):
            _make_resolution(decision=None, response=None)

    def test_blank_interrupt_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _make_resolution(interrupt_id="")

    def test_blank_resolved_by_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _make_resolution(resolved_by="")
