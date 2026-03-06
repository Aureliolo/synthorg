"""Agent run result model.

Frozen Pydantic model wrapping ``ExecutionResult`` with outer metadata
from the engine layer (system prompt, wall-clock duration, agent/task IDs).
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from ai_company.engine.prompt import SystemPrompt  # noqa: TC001


class AgentRunResult(BaseModel):
    """Immutable result of a complete agent engine run.

    Wraps the ``ExecutionResult`` from the loop with engine-level
    metadata: system prompt, wall-clock duration, and agent/task IDs.

    Attributes:
        execution_result: Outcome from the execution loop.
        system_prompt: System prompt used for this run.
        duration_seconds: Wall-clock run time in seconds.
        agent_id: Agent identifier (string form of UUID).
        task_id: Task identifier (always set currently; ``None``
            reserved for future taskless runs).
    """

    model_config = ConfigDict(frozen=True)

    execution_result: ExecutionResult = Field(
        description="Outcome from the execution loop",
    )
    system_prompt: SystemPrompt = Field(
        description="System prompt used for this run",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock run time in seconds",
    )
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task identifier, or None for future taskless runs",
    )

    # mypy does not yet model Pydantic's @computed_field + @property
    # combination correctly; the ignores are safe — Pydantic enforces
    # the return type at runtime.

    @computed_field(  # type: ignore[prop-decorator]
        description="Why the execution terminated",
    )
    @property
    def termination_reason(self) -> TerminationReason:
        """Why the execution loop terminated."""
        return self.execution_result.termination_reason

    @computed_field(  # type: ignore[prop-decorator]
        description="Total LLM turns completed",
    )
    @property
    def total_turns(self) -> int:
        """Number of turns completed during execution."""
        return len(self.execution_result.turns)

    @computed_field(  # type: ignore[prop-decorator]
        description="Total cost in USD",
    )
    @property
    def total_cost_usd(self) -> float:
        """Accumulated cost from the execution context."""
        return self.execution_result.context.accumulated_cost.cost_usd

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether the run completed successfully",
    )
    @property
    def is_success(self) -> bool:
        """True when termination reason is COMPLETED."""
        return self.termination_reason == TerminationReason.COMPLETED
