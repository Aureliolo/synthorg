"""Coordination domain models.

Frozen Pydantic models for coordination context, results,
execution waves, and phase tracking.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ai_company.core.agent import AgentIdentity  # noqa: TC001
from ai_company.core.enums import CoordinationTopology
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.engine.coordination.config import CoordinationConfig
from ai_company.engine.decomposition.models import (
    DecompositionContext,
    DecompositionResult,
    SubtaskStatusRollup,
)
from ai_company.engine.parallel_models import (
    ParallelExecutionResult,  # noqa: TC001
)
from ai_company.engine.routing.models import RoutingResult  # noqa: TC001
from ai_company.engine.workspace.models import (
    WorkspaceGroupResult,  # noqa: TC001
)


class CoordinationContext(BaseModel):
    """Input context for a multi-agent coordination run.

    Attributes:
        task: The parent task to decompose and execute.
        available_agents: Pool of agents available for assignment.
        decomposition_context: Constraints for decomposition.
        config: Coordination configuration.
    """

    model_config = ConfigDict(frozen=True)

    task: Task = Field(description="Parent task to coordinate")
    available_agents: tuple[AgentIdentity, ...] = Field(
        description="Agents available for assignment",
    )
    decomposition_context: DecompositionContext = Field(
        default_factory=DecompositionContext,
        description="Decomposition constraints",
    )
    config: CoordinationConfig = Field(
        default_factory=CoordinationConfig,
        description="Coordination configuration",
    )

    @model_validator(mode="after")
    def _validate_agents_non_empty(self) -> Self:
        """Ensure at least one agent is available."""
        if not self.available_agents:
            msg = "available_agents must contain at least one agent"
            raise ValueError(msg)
        return self


class CoordinationPhaseResult(BaseModel):
    """Result of a single coordination pipeline phase.

    Attributes:
        phase: Phase name (decompose, route, execute, etc.).
        success: Whether the phase completed successfully.
        duration_seconds: Wall-clock duration of the phase.
        error: Error description if the phase failed.
    """

    model_config = ConfigDict(frozen=True)

    phase: NotBlankStr = Field(description="Phase name")
    success: bool = Field(description="Whether phase succeeded")
    duration_seconds: float = Field(
        ge=0.0,
        description="Phase duration in seconds",
    )
    error: str | None = Field(
        default=None,
        description="Error description on failure",
    )

    @model_validator(mode="after")
    def _validate_success_error_consistency(self) -> Self:
        """Ensure success and error fields are consistent."""
        if self.success and self.error is not None:
            msg = "successful phase must not have an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed phase must have an error description"
            raise ValueError(msg)
        return self


class CoordinationWave(BaseModel):
    """A single execution wave within a coordination run.

    Attributes:
        wave_index: Zero-based wave index.
        subtask_ids: IDs of subtasks in this wave.
        execution_result: Result from ParallelExecutor, if executed.
    """

    model_config = ConfigDict(frozen=True)

    wave_index: int = Field(ge=0, description="Zero-based wave index")
    subtask_ids: tuple[NotBlankStr, ...] = Field(
        description="Subtask IDs in this wave",
    )
    execution_result: ParallelExecutionResult | None = Field(
        default=None,
        description="Parallel execution result",
    )

    @model_validator(mode="after")
    def _validate_subtask_ids_non_empty(self) -> Self:
        """Ensure at least one subtask ID is present."""
        if not self.subtask_ids:
            msg = "subtask_ids must contain at least one ID"
            raise ValueError(msg)
        return self


class CoordinationResult(BaseModel):
    """Result of a complete multi-agent coordination run.

    Attributes:
        parent_task_id: ID of the parent task.
        topology: Resolved coordination topology.
        decomposition_result: Result of task decomposition.
        routing_result: Result of task routing.
        phases: Phase results in execution order.
        waves: Execution waves with their results.
        status_rollup: Aggregated subtask status rollup.
        workspace_merge: Workspace merge result, if applicable.
        total_duration_seconds: Total wall-clock duration.
        total_cost_usd: Total cost across all waves.
    """

    model_config = ConfigDict(frozen=True)

    parent_task_id: NotBlankStr = Field(description="Parent task ID")
    topology: CoordinationTopology = Field(
        description="Resolved coordination topology",
    )
    decomposition_result: DecompositionResult | None = Field(
        default=None,
        description="Decomposition result",
    )
    routing_result: RoutingResult | None = Field(
        default=None,
        description="Routing result",
    )
    phases: tuple[CoordinationPhaseResult, ...] = Field(
        min_length=1,
        description="Phase results in execution order",
    )
    waves: tuple[CoordinationWave, ...] = Field(
        default=(),
        description="Execution waves",
    )
    status_rollup: SubtaskStatusRollup | None = Field(
        default=None,
        description="Aggregated subtask status rollup",
    )
    workspace_merge: WorkspaceGroupResult | None = Field(
        default=None,
        description="Workspace merge result",
    )
    total_duration_seconds: float = Field(
        ge=0.0,
        description="Total wall-clock duration",
    )
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost in USD",
    )

    @model_validator(mode="after")
    def _validate_topology_resolved(self) -> Self:
        """Ensure topology is resolved (not AUTO) in final result."""
        if self.topology == CoordinationTopology.AUTO:
            msg = "CoordinationResult topology must be resolved, not AUTO"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether all phases succeeded",
    )
    @property
    def is_success(self) -> bool:
        """True when every phase completed successfully."""
        return all(p.success for p in self.phases)
