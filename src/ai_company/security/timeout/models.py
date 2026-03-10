"""Timeout action model — the result of evaluating a timeout policy."""

from pydantic import BaseModel, ConfigDict, Field

from ai_company.core.enums import TimeoutActionType  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001


class TimeoutAction(BaseModel):
    """Action to take when an approval item times out.

    Attributes:
        action: The timeout action type (wait, approve, deny, escalate).
        reason: Human-readable explanation for the action.
        escalate_to: Target role/agent for escalation (only when
            action is ESCALATE).
    """

    model_config = ConfigDict(frozen=True)

    action: TimeoutActionType = Field(description="Timeout action type")
    reason: NotBlankStr = Field(description="Explanation for the action")
    escalate_to: NotBlankStr | None = Field(
        default=None,
        description="Escalation target (when action is ESCALATE)",
    )
