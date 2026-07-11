"""Work pipeline domain models.

Frozen Pydantic models for the spine's public entry contract
(:class:`WorkItem`), per-phase results, and the terminal
:class:`WorkPipelineResult`. All models are pure-serialisable: the
result reports identifiers and status, not heavyweight engine
objects, so the caller re-reads authoritative task / metrics state
from the existing stores (single source of truth).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr

# Terminal task states that mean the run did NOT succeed. A pipeline whose
# phases all executed but whose task ended here (e.g. a silent no-op failed
# by the fail-loud invariant) must not roll up as a successful dispatch.
_UNSUCCESSFUL_TASK_STATUSES: Final = frozenset(
    {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED}
)


class WorkSource(StrEnum):
    """Origin classification of a unit of work entering the spine.

    One member per known entry adapter. Every adapter that feeds the
    pipeline declares its source so provenance is preserved for
    metrics, the simulation harness, and audit.
    """

    SIMULATION = "simulation"
    INTAKE = "intake"
    TASK_BOARD = "task_board"
    OBJECTIVE = "objective"
    CONVERSATIONAL = "conversational"
    BROWNFIELD = "brownfield"


class RoutingVerdict(StrEnum):
    """Solo-vs-team decision owned by the decomposition layer.

    ``LEAF`` routes the work to a single agent; ``SPLITTABLE`` hands
    it to the multi-agent coordinator. Never a user choice.
    """

    LEAF = "leaf"
    SPLITTABLE = "splittable"


class ExecutionPath(StrEnum):
    """Which execution path the spine actually took."""

    SOLO = "solo"
    TEAM = "team"
    REFINEMENT = "refinement"
    PLAN_REVIEW = "plan_review"


class WorkItem(BaseModel):
    """The single public entry contract every adapter feeds the spine.

    A typed envelope mapping an adapter's intent onto the intake
    phase. Carries enough structure for a deterministic intake
    mapping (no NLP guessing) plus provenance for correlation.

    Attributes:
        origin_adapter_id: Identifier of the adapter that built this
            item (e.g. ``"simulation-harness"``, ``"task-board"``).
        source: Origin classification.
        title: Short human-readable work title.
        raw_intent: The detailed request / description body.
        project: Project the work belongs to.
        requested_by: Agent name or user id that requested the work.
        priority: Work priority.
        task_type: Classification of the work type.
        estimated_complexity: Complexity estimate (drives routing).
        acceptance_criteria: Optional acceptance criteria strings.
        correlation_id: End-to-end trace id (auto-generated if absent).
        created_at: Construction timestamp (tz-aware UTC).
        plan_required: When set, the spine always decomposes this brief
            into a plan and never runs it as a single solo leaf, whatever
            the solo-vs-team router decides.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    origin_adapter_id: NotBlankStr = Field(
        description="Identifier of the adapter that built this item",
    )
    source: WorkSource = Field(description="Origin classification")
    title: NotBlankStr = Field(description="Short human-readable work title")
    raw_intent: NotBlankStr = Field(
        description="Detailed request / description body",
    )
    project: NotBlankStr = Field(description="Project the work belongs to")
    requested_by: NotBlankStr = Field(
        description="Agent name or user id that requested the work",
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Work priority",
    )
    task_type: TaskType = Field(
        default=TaskType.DEVELOPMENT,
        description="Classification of the work type",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate (drives routing)",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional acceptance criteria strings",
    )
    correlation_id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="End-to-end trace id",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Construction timestamp (tz-aware UTC)",
    )
    forecast_id: UUID | None = Field(
        default=None,
        description=(
            "Identifier of the approved pre-flight cost forecast that"
            " released this work item into the pipeline (None when"
            " budget.forecast_required is disabled or when the gate"
            " has not yet attached one)"
        ),
    )
    hard_ceiling: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Per-run hard ceiling carried from the approved forecast's"
            " ceiling_amount. The intake phase stamps this onto the Task"
            " so the in-loop BudgetChecker enforces the operator-approved"
            " ceiling (None falls back to the global budget.run_hard_ceiling)"
        ),
    )
    plan_required: bool = Field(
        default=False,
        description=(
            "When True the spine always decomposes this brief into a plan"
            " rather than running it as a single solo leaf, regardless of the"
            " solo-vs-team router. Set by objective/charter entry adapters, for"
            " which a single task is never a valid outcome."
        ),
    )


class WorkPhaseResult(BaseModel):
    """Outcome of a single pipeline phase.

    Attributes:
        phase: Phase name.
        success: Whether the phase completed successfully.
        duration_seconds: Wall-clock duration of the phase.
        error: Scrubbed error description (only when not successful).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    phase: NotBlankStr = Field(description="Phase name")
    success: bool = Field(description="Whether the phase succeeded")
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock duration of the phase in seconds",
    )
    error: str | None = Field(
        default=None,
        description="Scrubbed error description (when not successful)",
    )

    @model_validator(mode="after")
    def _validate_success_error_consistency(self) -> Self:
        """Ensure ``success`` and ``error`` are mutually consistent.

        Returns:
            ``self`` unchanged when ``success`` / ``error`` agree.

        Raises:
            ValueError: When a successful phase has an error or a
                failed phase lacks one.
        """
        if self.success and self.error is not None:
            msg = "successful phase must not carry an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed phase must carry an error description"
            raise ValueError(msg)
        return self


class RefinementHandoff(BaseModel):
    """A handoff to human-in-the-loop refinement for under-specified work.

    Produced when team-bound work reaches the spine with no definition of
    done: instead of mobilising a team against undefined work, the spine
    opens a refinement conversation (the Chief of Staff clarifies, then
    parks concrete proposals for approval) and reports this handoff so the
    caller can point the human at the conversation. Nothing executes until
    the refined, criteria-bearing work is approved.

    Attributes:
        conversation_id: The refinement conversation to continue.
        needs_clarification: ``True`` when refinement asked a clarifying
            question (the human continues the conversation); ``False``
            when it parked one or more concrete proposals for approval.
        detail: Human-readable summary -- the clarifying question, or a
            description of what was parked.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    conversation_id: NotBlankStr = Field(
        description="The refinement conversation to continue",
    )
    needs_clarification: bool = Field(
        description="Whether refinement asked a clarifying question",
    )
    detail: NotBlankStr = Field(
        description="Human-readable summary of the refinement outcome",
    )


class PlanReviewHandoff(BaseModel):
    """A handoff to human plan approval before a team builds.

    Produced when splittable team work is decomposed into a plan (subtask
    tree) and the org runs with a human plan-approval gate: instead of
    dispatching the team immediately, the spine parks the plan for review
    and reports this handoff so the caller can point the human at the
    approval. Nothing builds until the plan is approved; on approval the
    exact approved plan is dispatched (no re-decomposition).

    Attributes:
        approval_id: The parked plan-approval item the human approves.
        subtask_count: Number of subtasks in the decomposed plan.
        detail: Human-readable summary of the plan awaiting approval.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr = Field(
        description="The parked plan-approval item to approve",
    )
    subtask_count: int = Field(
        ge=0,
        description="Number of subtasks in the decomposed plan",
    )
    detail: NotBlankStr = Field(
        description="Human-readable summary of the plan awaiting approval",
    )


class WorkPipelineResult(BaseModel):
    """Terminal result of a single work pipeline run.

    Reports identifiers and status only; the caller re-reads the
    authoritative task via the task engine and coordination metrics
    via the metrics store.

    Attributes:
        work_item: The original input envelope.
        verdict: The solo-vs-team decision.
        execution_path: Which path was taken.
        task_id: The task that was executed.
        final_task_status: Authoritative post-run task status.
        phases: Per-phase results in execution order (non-empty).
        refinement_handoff: Set iff ``execution_path`` is
            ``REFINEMENT`` -- the human-in-the-loop handoff that ran
            instead of team execution.
        plan_review_handoff: Set iff ``execution_path`` is
            ``PLAN_REVIEW`` -- the human plan-approval handoff parked
            instead of dispatching the team.
        is_success: Whether every recorded phase succeeded and the task
            did not end in a failure/cancelled/rejected terminal state.
        total_duration_seconds: Total wall-clock duration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    work_item: WorkItem = Field(description="The original input envelope")
    verdict: RoutingVerdict = Field(description="Solo-vs-team decision")
    execution_path: ExecutionPath = Field(description="Path taken")
    task_id: NotBlankStr = Field(description="Task that was executed")
    final_task_status: TaskStatus = Field(
        description="Authoritative post-run task status",
    )
    phases: tuple[WorkPhaseResult, ...] = Field(
        min_length=1,
        description="Per-phase results in execution order",
    )
    refinement_handoff: RefinementHandoff | None = Field(
        default=None,
        description="The refinement handoff (set iff path is REFINEMENT)",
    )
    plan_review_handoff: PlanReviewHandoff | None = Field(
        default=None,
        description="The plan-approval handoff (set iff path is PLAN_REVIEW)",
    )
    total_duration_seconds: float = Field(
        ge=0.0,
        description="Total wall-clock duration in seconds",
    )

    @model_validator(mode="after")
    def _validate_handoff_path_consistency(self) -> Self:
        """Ensure each handoff and ``execution_path`` agree.

        Returns:
            ``self`` unchanged when the handoffs match the path.

        Raises:
            ValueError: When a ``REFINEMENT``/``PLAN_REVIEW`` path lacks its
                handoff, or a path carries a handoff it should not.
        """
        is_refinement = self.execution_path is ExecutionPath.REFINEMENT
        if is_refinement and self.refinement_handoff is None:
            msg = "REFINEMENT execution_path requires a refinement_handoff"
            raise ValueError(msg)
        if not is_refinement and self.refinement_handoff is not None:
            msg = "refinement_handoff is only valid on the REFINEMENT path"
            raise ValueError(msg)
        is_plan_review = self.execution_path is ExecutionPath.PLAN_REVIEW
        if is_plan_review and self.plan_review_handoff is None:
            msg = "PLAN_REVIEW execution_path requires a plan_review_handoff"
            raise ValueError(msg)
        if not is_plan_review and self.plan_review_handoff is not None:
            msg = "plan_review_handoff is only valid on the PLAN_REVIEW path"
            raise ValueError(msg)
        return self

    @computed_field(
        description="Whether the run succeeded (all phases + task outcome)",
    )
    @property
    def is_success(self) -> bool:
        """Whether every phase succeeded and the task did not end in failure.

        Rolling the authoritative ``final_task_status`` into the verdict
        stops a run whose phases merely executed (e.g. a silent no-op the
        fail-loud invariant failed) from being reported as a successful
        dispatch.
        """
        return (
            all(phase.success for phase in self.phases)
            and self.final_task_status not in _UNSUCCESSFUL_TASK_STATUSES
        )
