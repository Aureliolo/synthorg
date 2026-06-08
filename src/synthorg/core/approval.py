"""Human approval item domain model.

Represents an action that requires human approval before proceeding.
Used by the approval queue API and referenced by engine and security subsystems.
"""

import copy
from typing import Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.evidence import EvidencePackage
from synthorg.core.types import NotBlankStr
from synthorg.ontology.decorator import ontology_entity


@ontology_entity(entity_name="Approval")
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
        source: Origin discriminator fixed at creation. Routes a
            decided approval deterministically (parked-context resume
            vs. review gate) without a live parked-context probe.
            Defaults to ``REVIEW_GATE``; the two park producers (SecOps
            escalation and the ``request_human_approval`` tool) set
            ``PARKED_CONTEXT``.
        consumed_at: When an APPROVED one-shot grant was spent. ``None``
            until consumed. The governed external-access tool sets this
            via an atomic compare-and-set (``consume_if_approved``)
            before egress so the same approval cannot authorise a second
            call; the approval keeps ``status == APPROVED`` because
            consumption is orthogonal to the decision lifecycle.
        metadata: Additional key-value metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    action_type: NotBlankStr
    title: NotBlankStr
    description: NotBlankStr
    requested_by: NotBlankStr
    risk_level: ApprovalRiskLevel
    source: ApprovalSource = ApprovalSource.REVIEW_GATE
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    decided_at: AwareDatetime | None = None
    decided_by: NotBlankStr | None = None
    decision_reason: NotBlankStr | None = None
    task_id: NotBlankStr | None = None
    consumed_at: AwareDatetime | None = None
    evidence_package: EvidencePackage | None = Field(
        default=None,
        description="Structured evidence for HITL approval",
    )
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy metadata to prevent external mutation.

        Returns:
            The instance with ``metadata`` deep-copied so a caller's
            original dict cannot mutate the frozen model.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self

    @model_validator(mode="after")
    def _validate_decision_fields(self) -> Self:
        """Enforce decision field invariants.

        - APPROVED/REJECTED require ``decided_at`` and ``decided_by``.
        - REJECTED additionally requires a non-empty ``decision_reason``.
        - PENDING/EXPIRED must NOT have ``decided_at`` or ``decided_by``.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If an APPROVED/REJECTED status lacks
                ``decided_at``/``decided_by``, a REJECTED status lacks a
                ``decision_reason``, or a non-decided status carries
                decision fields.
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
        """Ensure ``expires_at`` is after ``created_at`` when set.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``expires_at`` is set but not strictly after
                ``created_at``.
        """
        if self.expires_at is not None and self.expires_at <= self.created_at:
            msg = "expires_at must be after created_at"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_consumption(self) -> Self:
        """Enforce that ``consumed_at`` is only set on an APPROVED grant.

        Consumption is a spent APPROVED grant (orthogonal to the decision
        lifecycle), so a consumed approval can never be PENDING, REJECTED,
        or EXPIRED. Enforcing this at construction keeps the one-shot
        invariant a type guarantee, not just a store-side convention.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``consumed_at`` is set while the status is not
                APPROVED.
        """
        if self.consumed_at is not None and self.status is not ApprovalStatus.APPROVED:
            msg = (
                f"consumed_at may only be set when status is approved "
                f"(got {self.status.value})"
            )
            raise ValueError(msg)
        return self
