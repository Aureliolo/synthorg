"""Timeout action model — the result of evaluating a timeout policy."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import TimeoutActionType
from synthorg.core.types import NotBlankStr  # noqa: TC001


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

    @model_validator(mode="after")
    def _validate_escalate_to(self) -> Self:
        """Enforce ``escalate_to`` consistency with action type."""
        if self.action == TimeoutActionType.ESCALATE and self.escalate_to is None:
            msg = "escalate_to is required when action is ESCALATE"
            raise ValueError(msg)
        if self.action != TimeoutActionType.ESCALATE and self.escalate_to is not None:
            msg = (
                f"escalate_to must be None when action is "
                f"{self.action.value!r}, got {self.escalate_to!r}"
            )
            raise ValueError(msg)
        return self
