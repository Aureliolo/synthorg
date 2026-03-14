"""Approval gate models — escalation info and resume payload.

These frozen Pydantic models carry escalation details from SecOps
ESCALATE verdicts or ``request_human_approval`` tool calls, and
approval decision payloads for resume injection.
"""

from pydantic import BaseModel, ConfigDict

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class EscalationInfo(BaseModel):
    """Escalation details from SecOps ESCALATE or request_human_approval.

    Attributes:
        approval_id: The approval item identifier.
        tool_call_id: LLM tool call identifier.
        tool_name: Name of the tool that triggered escalation.
        action_type: Security action type (``category:action`` format).
        risk_level: Assessed risk level for the action.
        reason: Human-readable explanation of why escalation is needed.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: NotBlankStr
    tool_call_id: NotBlankStr
    tool_name: NotBlankStr
    action_type: NotBlankStr
    risk_level: ApprovalRiskLevel
    reason: NotBlankStr


class ResumePayload(BaseModel):
    """Approval decision payload for resume injection.

    Attributes:
        approval_id: The approval item identifier.
        approved: Whether the action was approved.
        decided_by: Who made the decision.
        decision_reason: Optional reason for the decision.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: NotBlankStr
    approved: bool
    decided_by: NotBlankStr
    decision_reason: NotBlankStr | None = None
