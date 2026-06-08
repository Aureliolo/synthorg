"""Pruning domain models.

Frozen Pydantic models for pruning evaluations, requests, records,
and service configuration.
"""

from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot


class PruningEvaluation(BaseModel):
    """Result of a pruning policy evaluation.

    Attributes:
        agent_id: Agent being evaluated.
        eligible: Whether agent should be pruned.
        reasons: Human-readable justifications.
        scores: Debug scores from evaluation criteria.
        policy_name: Which policy produced this evaluation.
        snapshot: Performance snapshot used for evaluation.
        evaluated_at: When evaluation occurred.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    eligible: bool = Field(description="Whether agent should be pruned")
    reasons: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Human-readable justifications",
    )
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Debug scores from evaluation criteria",
    )
    policy_name: NotBlankStr = Field(
        description="Which policy produced this evaluation",
    )
    snapshot: AgentPerformanceSnapshot = Field(
        description="Performance snapshot used for evaluation",
    )
    evaluated_at: AwareDatetime = Field(description="When evaluation occurred")

    @model_validator(mode="after")
    def _validate_eligible_reasons(self) -> Self:
        """Eligible evaluations must have at least one reason.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.eligible and not self.reasons:
            msg = "eligible evaluations must have at least one reason"
            raise ValueError(msg)
        return self


class PruningRequest(BaseModel):
    """Request to prune an agent pending human approval.

    Attributes:
        id: Unique request identifier.
        agent_id: Agent to be pruned.
        agent_name: Agent's display name.
        evaluation: The evaluation result.
        approval_id: Associated approval item ID.
        status: Current approval status.
        created_at: When request was created.
        decided_at: When approval decision was made.
        decided_by: Who made the approval decision.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique request identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent to be pruned")
    agent_name: NotBlankStr = Field(description="Agent display name")
    evaluation: PruningEvaluation = Field(description="Evaluation result")
    approval_id: NotBlankStr = Field(description="Associated approval item ID")
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Current approval status",
    )
    created_at: AwareDatetime = Field(description="When request was created")
    decided_at: AwareDatetime | None = Field(
        default=None,
        description="When approval decision was made",
    )
    decided_by: NotBlankStr | None = Field(
        default=None,
        description="Who made the approval decision",
    )

    @model_validator(mode="after")
    def _validate_decision_fields(self) -> Self:
        """Enforce decision field invariants.

        - PENDING/EXPIRED must have decided_at and decided_by as None.
        - APPROVED/REJECTED require decided_at and decided_by.
        - decided_at must be >= created_at when present.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        decided_statuses = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
        }

        if self.status in decided_statuses:
            if self.decided_at is None or self.decided_by is None:
                msg = (
                    f"decided_at and decided_by are required "
                    f"when status is {self.status.value}"
                )
                raise ValueError(msg)
        elif self.decided_at is not None or self.decided_by is not None:
            msg = (
                f"decided_at and decided_by must be None "
                f"when status is {self.status.value}"
            )
            raise ValueError(msg)

        if self.decided_at is not None and self.decided_at < self.created_at:
            msg = (
                f"decided_at ({self.decided_at}) must be >= "
                f"created_at ({self.created_at})"
            )
            raise ValueError(msg)
        return self


class PruningRecord(BaseModel):
    """Record of a completed pruning process.

    Attributes:
        agent_id: Agent who was pruned.
        agent_name: Agent's display name.
        pruning_request_id: Associated pruning request.
        firing_request_id: Firing request ID from offboarding.
        reason: Why agent was pruned.
        approval_id: Which approval authorized it.
        initiated_by: System or human who initiated.
        created_at: When process started.
        completed_at: When process finished.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent who was pruned")
    agent_name: NotBlankStr = Field(description="Agent display name")
    pruning_request_id: NotBlankStr = Field(
        description="Associated pruning request",
    )
    firing_request_id: NotBlankStr = Field(
        description="Firing request ID from offboarding",
    )
    reason: NotBlankStr = Field(description="Why agent was pruned")
    approval_id: NotBlankStr = Field(description="Approval that authorized it")
    initiated_by: NotBlankStr = Field(description="Who initiated the pruning")
    created_at: AwareDatetime = Field(description="When process started")
    completed_at: AwareDatetime = Field(description="When process finished")

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure completed_at >= created_at.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.completed_at < self.created_at:
            msg = (
                f"completed_at ({self.completed_at}) must be >= "
                f"created_at ({self.created_at})"
            )
            raise ValueError(msg)
        return self


class PruningJobRun(BaseModel):
    """Metadata about a single pruning scheduler cycle.

    Attributes:
        job_id: Unique cycle identifier.
        run_at: When the cycle started.
        agents_evaluated: Count of agents checked.
        agents_eligible: Count found eligible for pruning.
        approval_requests_created: Count of new approvals.
        elapsed_seconds: How long the cycle took.
        errors: Non-fatal errors encountered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    job_id: NotBlankStr = Field(description="Unique cycle identifier")
    run_at: AwareDatetime = Field(description="When the cycle started")
    agents_evaluated: int = Field(ge=0, description="Agents checked")
    agents_eligible: int = Field(ge=0, description="Agents eligible for pruning")
    approval_requests_created: int = Field(
        ge=0,
        description="New approvals created",
    )
    elapsed_seconds: float = Field(ge=0.0, description="Cycle duration")
    errors: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Non-fatal errors encountered",
    )

    @model_validator(mode="after")
    def _validate_count_relationships(self) -> Self:
        """Ensure count relationships are logically consistent.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.agents_eligible > self.agents_evaluated:
            msg = (
                f"agents_eligible ({self.agents_eligible}) cannot exceed "
                f"agents_evaluated ({self.agents_evaluated})"
            )
            raise ValueError(msg)
        if self.approval_requests_created > self.agents_eligible:
            msg = (
                f"approval_requests_created "
                f"({self.approval_requests_created}) cannot exceed "
                f"agents_eligible ({self.agents_eligible})"
            )
            raise ValueError(msg)
        return self


class PruningServiceConfig(BaseModel):
    """Configuration for the pruning service.

    Attributes:
        evaluation_interval_seconds: How often to run pruning cycles.
        max_approvals_per_cycle: Limit on approvals created per cycle.
        approval_expiry_days: Days until pending approval expires.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    evaluation_interval_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        description="How often to run pruning cycles",
    )
    max_approvals_per_cycle: int = Field(
        default=5,
        ge=1,
        description="Limit on approvals created per cycle",
    )
    approval_expiry_days: int = Field(
        default=7,
        ge=1,
        description="Days until pending approval expires",
    )
