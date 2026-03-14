"""HR domain models.

Frozen Pydantic models for hiring, firing, onboarding, offboarding,
and agent lifecycle events.
"""

from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.enums import SeniorityLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import (
    FiringReason,
    HiringRequestStatus,
    LifecycleEventType,
    OnboardingStep,
)


class CandidateCard(BaseModel):
    """Generated candidate for a hiring request.

    Attributes:
        id: Unique candidate identifier.
        name: Proposed agent name.
        role: Proposed role.
        department: Target department.
        level: Proposed seniority level.
        skills: Agent skills.
        rationale: Why this candidate was generated.
        estimated_monthly_cost: Estimated monthly cost in USD.
        template_source: Template used for generation, if any.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique candidate identifier",
    )
    name: NotBlankStr = Field(description="Proposed agent name")
    role: NotBlankStr = Field(description="Proposed role")
    department: NotBlankStr = Field(description="Target department")
    level: SeniorityLevel = Field(description="Proposed seniority level")
    skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent skills",
    )
    rationale: NotBlankStr = Field(description="Generation rationale")
    estimated_monthly_cost: float = Field(
        ge=0.0,
        description="Estimated monthly cost in USD",
    )
    template_source: NotBlankStr | None = Field(
        default=None,
        description="Template used for generation",
    )


class HiringRequest(BaseModel):
    """Request to hire a new agent.

    Attributes:
        id: Unique request identifier.
        requested_by: Agent or human who initiated the request.
        department: Target department.
        role: Desired role.
        level: Desired seniority level.
        required_skills: Skills the candidate must have.
        reason: Business justification.
        budget_limit_monthly: Maximum monthly cost, if constrained.
        template_name: Template to use for candidate generation.
        status: Current request status.
        created_at: When the request was created.
        candidates: Generated candidate cards.
        selected_candidate_id: ID of the chosen candidate.
        approval_id: ID of the associated approval item.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique request identifier",
    )
    requested_by: NotBlankStr = Field(description="Request initiator")
    department: NotBlankStr = Field(description="Target department")
    role: NotBlankStr = Field(description="Desired role")
    level: SeniorityLevel = Field(description="Desired seniority level")
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Required skills",
    )
    reason: NotBlankStr = Field(description="Business justification")
    budget_limit_monthly: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum monthly cost",
    )
    template_name: NotBlankStr | None = Field(
        default=None,
        description="Template for candidate generation",
    )
    status: HiringRequestStatus = Field(
        default=HiringRequestStatus.PENDING,
        description="Current request status",
    )
    created_at: AwareDatetime = Field(description="When the request was created")
    candidates: tuple[CandidateCard, ...] = Field(
        default=(),
        description="Generated candidate cards",
    )
    selected_candidate_id: NotBlankStr | None = Field(
        default=None,
        description="Chosen candidate ID",
    )
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Associated approval item ID",
    )

    @model_validator(mode="after")
    def _validate_status_candidate_consistency(self) -> Self:
        """Ensure status-dependent candidate constraints."""
        needs_candidate = {
            HiringRequestStatus.INSTANTIATED,
            HiringRequestStatus.APPROVED,
        }
        if self.status in needs_candidate and self.selected_candidate_id is None:
            msg = f"{self.status.value} requests must have a selected_candidate_id"
            raise ValueError(msg)
        if self.selected_candidate_id is not None:
            candidate_ids = {str(c.id) for c in self.candidates}
            if self.selected_candidate_id not in candidate_ids:
                msg = (
                    f"selected_candidate_id {self.selected_candidate_id!r} "
                    f"not found in candidates"
                )
                raise ValueError(msg)
        return self


class FiringRequest(BaseModel):
    """Request to terminate an agent.

    Attributes:
        id: Unique request identifier.
        agent_id: Agent to be terminated.
        agent_name: Agent's display name.
        reason: Reason for termination.
        requested_by: Initiator of the firing.
        details: Additional context.
        created_at: When the request was created.
        completed_at: When the firing was completed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique request identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent to terminate")
    agent_name: NotBlankStr = Field(description="Agent display name")
    reason: FiringReason = Field(description="Reason for termination")
    requested_by: NotBlankStr = Field(description="Firing initiator")
    details: str = Field(default="", description="Additional context")
    created_at: AwareDatetime = Field(description="When the request was created")
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="When the firing was completed",
    )

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure completed_at >= created_at when both are present."""
        if self.completed_at is not None and self.completed_at < self.created_at:
            msg = (
                f"completed_at ({self.completed_at}) must be >= "
                f"created_at ({self.created_at})"
            )
            raise ValueError(msg)
        return self


class OnboardingStepRecord(BaseModel):
    """Record of a single onboarding step.

    Attributes:
        step: The onboarding step.
        completed: Whether this step is complete.
        completed_at: When this step was completed.
        notes: Optional notes from the step.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    step: OnboardingStep = Field(description="The onboarding step")
    completed: bool = Field(default=False, description="Whether step is complete")
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="When completed",
    )
    notes: str = Field(default="", description="Step notes")

    @model_validator(mode="after")
    def _validate_completed_consistency(self) -> Self:
        """Ensure completed and completed_at are consistent."""
        if self.completed and self.completed_at is None:
            msg = "completed_at must be set when completed is True"
            raise ValueError(msg)
        if not self.completed and self.completed_at is not None:
            msg = "completed_at must be None when completed is False"
            raise ValueError(msg)
        return self


class OnboardingChecklist(BaseModel):
    """Agent onboarding checklist tracking all steps.

    Attributes:
        agent_id: Agent being onboarded.
        steps: Individual step records.
        started_at: When onboarding began.
        completed_at: When all steps were completed.
        is_complete: Whether all steps are done (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent being onboarded")
    steps: tuple[OnboardingStepRecord, ...] = Field(
        min_length=1,
        description="Individual step records",
    )
    started_at: AwareDatetime = Field(description="When onboarding began")
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="When all steps were completed",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        """Whether all onboarding steps are completed."""
        return all(s.completed for s in self.steps)

    @model_validator(mode="after")
    def _validate_completion_consistency(self) -> Self:
        """Ensure completed_at and step completion status are consistent."""
        all_done = all(s.completed for s in self.steps)
        if all_done and self.completed_at is None:
            msg = "completed_at must be set when all steps are completed"
            raise ValueError(msg)
        if not all_done and self.completed_at is not None:
            msg = "completed_at must be None when not all steps are completed"
            raise ValueError(msg)
        if self.completed_at is not None and self.completed_at < self.started_at:
            msg = (
                f"completed_at ({self.completed_at}) must be >= "
                f"started_at ({self.started_at})"
            )
            raise ValueError(msg)
        return self


class OffboardingRecord(BaseModel):
    """Record of a completed offboarding process.

    Attributes:
        agent_id: Agent who was offboarded.
        agent_name: Agent's display name.
        firing_request_id: Associated firing request.
        tasks_reassigned: IDs of reassigned tasks.
        memory_archive_id: ID of the memory archive, if created.
        org_memories_promoted: Number of memories promoted to org.
        team_notification_sent: Whether team was notified.
        started_at: When offboarding started.
        completed_at: When offboarding finished.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent who was offboarded")
    agent_name: NotBlankStr = Field(description="Agent display name")
    firing_request_id: NotBlankStr = Field(description="Associated firing request")
    tasks_reassigned: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of reassigned tasks",
    )
    memory_archive_id: NotBlankStr | None = Field(
        default=None,
        description="Memory archive ID",
    )
    org_memories_promoted: int = Field(
        default=0,
        ge=0,
        description="Memories promoted to org",
    )
    team_notification_sent: bool = Field(
        default=False,
        description="Whether team was notified",
    )
    started_at: AwareDatetime = Field(description="When offboarding started")
    completed_at: AwareDatetime = Field(description="When offboarding finished")

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure completed_at >= started_at."""
        if self.completed_at < self.started_at:
            msg = (
                f"completed_at ({self.completed_at}) must be >= "
                f"started_at ({self.started_at})"
            )
            raise ValueError(msg)
        return self


class AgentLifecycleEvent(BaseModel):
    """Record of an agent lifecycle event.

    Attributes:
        id: Unique event identifier.
        agent_id: Agent the event relates to.
        agent_name: Agent's display name.
        event_type: Type of lifecycle event.
        timestamp: When the event occurred.
        initiated_by: Who triggered the event.
        details: Human-readable event details.
        metadata: Additional structured metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique event identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent the event relates to")
    agent_name: NotBlankStr = Field(description="Agent display name")
    event_type: LifecycleEventType = Field(description="Type of lifecycle event")
    timestamp: AwareDatetime = Field(description="When the event occurred")
    initiated_by: NotBlankStr = Field(description="Who triggered the event")
    details: str = Field(default="", description="Event details")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Additional structured metadata",
    )
