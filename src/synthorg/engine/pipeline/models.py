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
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import (
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


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
        """Ensure ``success`` and ``error`` are mutually consistent."""
        if self.success and self.error is not None:
            msg = "successful phase must not carry an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed phase must carry an error description"
            raise ValueError(msg)
        return self


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
        is_success: Whether every recorded phase succeeded.
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
    total_duration_seconds: float = Field(
        ge=0.0,
        description="Total wall-clock duration in seconds",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether every recorded phase succeeded",
    )
    @property
    def is_success(self) -> bool:
        """Derived: ``True`` only if every recorded phase succeeded."""
        return all(phase.success for phase in self.phases)
