"""Compact, redacted snapshot of an ``AgentContext`` for reporting.

Separated from the live :class:`~synthorg.engine.context.AgentContext` so the
reporting DTO (used by crash recovery and procedural memory) has its own home and
the execution-context module stays focused on runtime state transitions. The
snapshot deliberately excludes conversation message contents to keep prompts and
tool outputs out of audit/recovery surfaces.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_execution import TaskExecution
from synthorg.providers.models import TokenUsage


@runtime_checkable
class SnapshotSource(Protocol):
    """What a snapshot reads off a live context.

    Structural rather than an ``AgentContext`` import: the context module
    imports this one, so naming the class here would close the cycle.
    """

    @property
    def execution_id(self) -> str:
        """Unique identifier of this execution run."""
        ...

    @property
    def turn_count(self) -> int:
        """Turns completed so far."""
        ...

    @property
    def accumulated_cost(self) -> TokenUsage:
        """Running cost and token totals."""
        ...

    @property
    def started_at(self) -> datetime:
        """When the execution began."""
        ...

    @property
    def conversation(self) -> Sequence[object]:
        """Messages so far; only the count reaches the snapshot."""
        ...

    @property
    def context_fill_tokens(self) -> int:
        """Estimated tokens currently in the context."""
        ...

    @property
    def task_execution(self) -> TaskExecution | None:
        """The task being executed, when one is active."""
        ...

    @property
    def context_fill_percent(self) -> float | None:
        """Share of the context window in use, when the capacity is known."""
        ...


class AgentContextSnapshot(BaseModel):
    """Compact frozen snapshot of an ``AgentContext`` for reporting.

    Attributes:
        execution_id: Unique execution run identifier.
        agent_id: Agent identifier (string form of UUID).
        task_id: Task identifier, if a task is active.
        turn_count: Number of turns completed.
        accumulated_cost: Running cost totals.
        task_status: Current task status, if a task is active.
        started_at: When the execution began.
        snapshot_at: When this snapshot was taken.
        message_count: Number of messages in the conversation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr = Field(description="Unique execution identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task identifier",
    )
    turn_count: int = Field(ge=0, description="Turns completed")
    accumulated_cost: TokenUsage = Field(
        description="Running cost totals",
    )
    task_status: TaskStatus | None = Field(
        default=None,
        description="Current task status",
    )
    started_at: AwareDatetime = Field(description="Execution start time")
    snapshot_at: AwareDatetime = Field(
        description="When snapshot was taken",
    )
    message_count: int = Field(ge=0, description="Messages in conversation")
    context_fill_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated context fill tokens",
    )
    context_fill_percent: float | None = Field(
        default=None,
        description="Context fill percentage",
    )

    @model_validator(mode="after")
    def _validate_task_pair(self) -> AgentContextSnapshot:
        """Ensure task_id and task_status are both set or both None.

        Returns:
            ``self`` unchanged when ``task_id`` and ``task_status`` agree.

        Raises:
            ValueError: When exactly one of the pair is set.
        """
        if (self.task_id is None) != (self.task_status is None):
            msg = "task_id and task_status must both be set or both be None"
            raise ValueError(msg)
        return self


def build_context_snapshot(
    source: SnapshotSource,
    *,
    agent_id: str,
) -> AgentContextSnapshot:
    """Project a live context onto its reporting snapshot.

    Args:
        source: The live context being snapshotted.
        agent_id: String form of the running agent's identifier, which the
            context holds on its identity rather than on itself.

    Returns:
        Frozen snapshot of the context's current state.
    """
    execution = source.task_execution
    return AgentContextSnapshot(
        execution_id=source.execution_id,
        agent_id=agent_id,
        task_id=str(execution.task.id) if execution is not None else None,
        turn_count=source.turn_count,
        accumulated_cost=source.accumulated_cost,
        task_status=execution.status if execution is not None else None,
        started_at=source.started_at,
        snapshot_at=datetime.now(UTC),
        message_count=len(source.conversation),
        context_fill_tokens=source.context_fill_tokens,
        context_fill_percent=source.context_fill_percent,
    )
