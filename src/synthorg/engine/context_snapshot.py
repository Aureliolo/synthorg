"""Compact, redacted snapshot of an ``AgentContext`` for reporting.

Separated from the live :class:`~synthorg.engine.context.AgentContext` so the
reporting DTO (used by crash recovery and procedural memory) has its own home and
the execution-context module stays focused on runtime state transitions. The
snapshot deliberately excludes conversation message contents to keep prompts and
tool outputs out of audit/recovery surfaces.
"""

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.providers.models import TokenUsage


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
