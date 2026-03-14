"""Human approval item domain model.

Represents an action that requires human approval before proceeding.
Used by the approval queue API and referenced by engine and security subsystems.
"""

from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ApprovalItem(BaseModel):
    """A single item in the human approval queue.

    Attributes:
        id: Unique approval identifier.
        action_type: What kind of action requires approval.
        title: Short summary of the approval request.
        description: Detailed explanation.
        requested_by: Agent or system that requested approval.
        risk_level: Assessed risk level.
        status: Current approval status.
        created_at: When the item was created.
        expires_at: Optional expiration time for auto-expiry.
        decided_at: When the decision was made (set on approve/reject).
        decided_by: Who made the decision (set on approve/reject).
        decision_reason: Reason for the decision (required on reject).
        task_id: Optional associated task identifier.
        metadata: Additional key-value metadata.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr
    action_type: NotBlankStr
    title: NotBlankStr
    description: NotBlankStr
    requested_by: NotBlankStr
    risk_level: ApprovalRiskLevel
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    decided_at: AwareDatetime | None = None
    decided_by: NotBlankStr | None = None
    decision_reason: NotBlankStr | None = None
    task_id: NotBlankStr | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_decision_fields(self) -> Self:
        """Enforce decision field invariants.

        - APPROVED/REJECTED require ``decided_at`` and ``decided_by``.
        - REJECTED additionally requires a non-empty ``decision_reason``.
        - PENDING/EXPIRED must NOT have ``decided_at`` or ``decided_by``.
        """
        decided_statuses = {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}

        if self.status in decided_statuses:
            if self.decided_at is None or self.decided_by is None:
                msg = (
                    f"decided_at and decided_by are required "
                    f"when status is {self.status.value}"
                )
                raise ValueError(msg)
            if self.status == ApprovalStatus.REJECTED and not self.decision_reason:
                msg = "decision_reason is required when status is rejected"
                raise ValueError(msg)
        elif self.decided_at is not None or self.decided_by is not None:
            msg = (
                f"decided_at and decided_by must be None "
                f"when status is {self.status.value}"
            )
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_expiry(self) -> Self:
        """Ensure ``expires_at`` is after ``created_at`` when set."""
        if self.expires_at is not None and self.expires_at <= self.created_at:
            msg = "expires_at must be after created_at"
            raise ValueError(msg)
        return self
