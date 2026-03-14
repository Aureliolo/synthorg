"""Tests for approval gate models — EscalationInfo and ResumePayload."""

import pytest
from pydantic import ValidationError

from ai_company.core.enums import ApprovalRiskLevel
from ai_company.engine.approval_gate_models import EscalationInfo, ResumePayload

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestEscalationInfo:
    """EscalationInfo construction and immutability."""

    def test_valid_construction(self) -> None:
        info = EscalationInfo(
            approval_id="approval-1",
            tool_call_id="tc-1",
            tool_name="deploy_to_prod",
            action_type="deploy:production",
            risk_level=ApprovalRiskLevel.CRITICAL,
            reason="Production deployment requires approval",
        )
        assert info.approval_id == "approval-1"
        assert info.tool_call_id == "tc-1"
        assert info.tool_name == "deploy_to_prod"
        assert info.action_type == "deploy:production"
        assert info.risk_level == ApprovalRiskLevel.CRITICAL
        assert info.reason == "Production deployment requires approval"

    def test_frozen_immutability(self) -> None:
        info = EscalationInfo(
            approval_id="approval-1",
            tool_call_id="tc-1",
            tool_name="deploy_to_prod",
            action_type="deploy:production",
            risk_level=ApprovalRiskLevel.HIGH,
            reason="Needs approval",
        )
        with pytest.raises(ValidationError):
            info.approval_id = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field",
        ["approval_id", "tool_call_id", "tool_name", "action_type", "reason"],
    )
    def test_blank_string_rejected(self, field: str) -> None:
        kwargs = {
            "approval_id": "approval-1",
            "tool_call_id": "tc-1",
            "tool_name": "deploy_to_prod",
            "action_type": "deploy:production",
            "risk_level": ApprovalRiskLevel.LOW,
            "reason": "Needs approval",
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            EscalationInfo(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field",
        ["approval_id", "tool_call_id", "tool_name", "action_type", "reason"],
    )
    def test_empty_string_rejected(self, field: str) -> None:
        kwargs = {
            "approval_id": "approval-1",
            "tool_call_id": "tc-1",
            "tool_name": "deploy_to_prod",
            "action_type": "deploy:production",
            "risk_level": ApprovalRiskLevel.LOW,
            "reason": "Needs approval",
        }
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            EscalationInfo(**kwargs)  # type: ignore[arg-type]

    def test_all_risk_levels_accepted(self) -> None:
        for level in ApprovalRiskLevel:
            info = EscalationInfo(
                approval_id="a",
                tool_call_id="t",
                tool_name="tool",
                action_type="cat:act",
                risk_level=level,
                reason="reason",
            )
            assert info.risk_level == level


class TestResumePayload:
    """ResumePayload construction and immutability."""

    def test_approved_without_reason(self) -> None:
        payload = ResumePayload(
            approval_id="approval-1",
            approved=True,
            decided_by="admin",
        )
        assert payload.approval_id == "approval-1"
        assert payload.approved is True
        assert payload.decided_by == "admin"
        assert payload.decision_reason is None

    def test_rejected_with_reason(self) -> None:
        payload = ResumePayload(
            approval_id="approval-1",
            approved=False,
            decided_by="admin",
            decision_reason="Too risky",
        )
        assert payload.approved is False
        assert payload.decision_reason == "Too risky"

    def test_frozen_immutability(self) -> None:
        payload = ResumePayload(
            approval_id="approval-1",
            approved=True,
            decided_by="admin",
        )
        with pytest.raises(ValidationError):
            payload.approved = False  # type: ignore[misc]

    @pytest.mark.parametrize("field", ["approval_id", "decided_by"])
    def test_blank_string_rejected(self, field: str) -> None:
        kwargs = {
            "approval_id": "approval-1",
            "approved": True,
            "decided_by": "admin",
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            ResumePayload(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize("field", ["approval_id", "decided_by"])
    def test_empty_string_rejected(self, field: str) -> None:
        kwargs = {
            "approval_id": "approval-1",
            "approved": True,
            "decided_by": "admin",
        }
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            ResumePayload(**kwargs)  # type: ignore[arg-type]

    def test_empty_decision_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResumePayload(
                approval_id="approval-1",
                approved=False,
                decided_by="admin",
                decision_reason="",
            )

    def test_blank_decision_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResumePayload(
                approval_id="approval-1",
                approved=False,
                decided_by="admin",
                decision_reason="   ",
            )
