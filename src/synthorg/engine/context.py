"""Agent execution context.

Wraps an ``AgentIdentity`` (frozen config) with evolving runtime state
(conversation, cost, turn count, task execution) using
``model_copy(update=...)`` for cheap, immutable state transitions.
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.errors import ExecutionStateError, MaxTurnsExceededError
from synthorg.engine.task_execution import TaskExecution
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_CREATED,
    EXECUTION_CONTEXT_NO_TASK,
    EXECUTION_CONTEXT_SNAPSHOT,
    EXECUTION_CONTEXT_TRANSITION_FAILED,
    EXECUTION_CONTEXT_TURN,
    EXECUTION_MAX_TURNS_EXCEEDED,
)
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    ChatMessage,
    TokenUsage,
    add_token_usage,
)

logger = get_logger(__name__)

DEFAULT_MAX_TURNS: int = 20
"""Default hard limit on LLM turns per agent execution."""


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

    model_config = ConfigDict(frozen=True)

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

    @model_validator(mode="after")
    def _validate_task_pair(self) -> AgentContextSnapshot:
        """Ensure task_id and task_status are both set or both None."""
        if (self.task_id is None) != (self.task_status is None):
            msg = "task_id and task_status must both be set or both be None"
            raise ValueError(msg)
        return self


class AgentContext(BaseModel):
    """Frozen runtime context for agent execution.

    All state evolution happens via ``model_copy(update=...)``.
    The context tracks the conversation, accumulated cost, and
    optionally a ``TaskExecution`` for task-bound agent runs.

    Attributes:
        execution_id: Unique identifier for this execution run.
        identity: Frozen agent identity configuration.
        task_execution: Current task execution state (if any).
        conversation: Accumulated chat messages.
        accumulated_cost: Running token usage and cost totals.
        turn_count: Number of LLM turns completed.
        max_turns: Hard limit on turns before the engine stops.
        started_at: When this execution began.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: NotBlankStr = Field(
        description="Unique execution run identifier",
    )
    identity: AgentIdentity = Field(
        description="Frozen agent identity config",
    )
    task_execution: TaskExecution | None = Field(
        default=None,
        description="Current task execution state",
    )
    conversation: tuple[ChatMessage, ...] = Field(
        default=(),
        description="Accumulated conversation messages",
    )
    accumulated_cost: TokenUsage = Field(
        default=ZERO_TOKEN_USAGE,
        description="Running cost totals across all turns",
    )
    turn_count: int = Field(
        default=0,
        ge=0,
        description="Turns completed",
    )
    max_turns: int = Field(
        default=DEFAULT_MAX_TURNS,
        gt=0,
        description="Hard turn limit",
    )
    started_at: AwareDatetime = Field(
        description="When execution began",
    )

    @classmethod
    def from_identity(
        cls,
        identity: AgentIdentity,
        *,
        task: Task | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> AgentContext:
        """Create a fresh execution context from an agent identity.

        Args:
            identity: The frozen agent identity card.
            task: Optional task to bind to this execution.
            max_turns: Maximum number of LLM turns allowed.

        Returns:
            New ``AgentContext`` ready for execution.
        """
        task_execution = TaskExecution.from_task(task) if task is not None else None
        context = cls(
            execution_id=str(uuid4()),
            identity=identity,
            task_execution=task_execution,
            max_turns=max_turns,
            started_at=datetime.now(UTC),
        )
        logger.debug(
            EXECUTION_CONTEXT_CREATED,
            execution_id=context.execution_id,
            agent_id=str(identity.id),
            has_task=task is not None,
        )
        return context

    def with_message(self, msg: ChatMessage) -> AgentContext:
        """Append a single message to the conversation.

        Args:
            msg: The chat message to append.

        Returns:
            New ``AgentContext`` with the message appended.
        """
        return self.model_copy(update={"conversation": (*self.conversation, msg)})

    def with_turn_completed(
        self,
        usage: TokenUsage,
        response_msg: ChatMessage,
    ) -> AgentContext:
        """Record a completed turn.

        Increments turn count, appends the response message, and
        accumulates cost on both the context and the task execution
        (if present).

        Args:
            usage: Token usage from this turn's LLM call.
            response_msg: The assistant's response message.

        Returns:
            New ``AgentContext`` with updated state.

        Raises:
            MaxTurnsExceededError: If ``max_turns`` has been reached.
        """
        if not self.has_turns_remaining:
            msg = (
                f"Agent {self.identity.id} exceeded max_turns "
                f"({self.max_turns}) for execution {self.execution_id}"
            )
            logger.error(
                EXECUTION_MAX_TURNS_EXCEEDED,
                execution_id=self.execution_id,
                agent_id=str(self.identity.id),
                max_turns=self.max_turns,
                turn_count=self.turn_count,
            )
            raise MaxTurnsExceededError(msg)
        updates: dict[str, object] = {
            "turn_count": self.turn_count + 1,
            "conversation": (*self.conversation, response_msg),
            "accumulated_cost": add_token_usage(self.accumulated_cost, usage),
        }
        if self.task_execution is not None:
            updates["task_execution"] = self.task_execution.with_cost(usage)

        result = self.model_copy(update=updates)
        logger.info(
            EXECUTION_CONTEXT_TURN,
            execution_id=self.execution_id,
            turn=result.turn_count,
            cost_usd=usage.cost_usd,
        )
        return result

    def with_task_transition(
        self,
        target: TaskStatus,
        *,
        reason: str = "",
    ) -> AgentContext:
        """Transition the task execution status.

        Delegates to
        :meth:`~synthorg.engine.task_execution.TaskExecution.with_transition`.

        Args:
            target: The desired target status.
            reason: Optional reason for the transition.

        Returns:
            New ``AgentContext`` with updated task execution.

        Raises:
            ExecutionStateError: If no task execution is set.
            ValueError: If the transition is invalid (from
                ``validate_transition``).
        """
        if self.task_execution is None:
            msg = "Cannot transition task status: no task execution is set"
            logger.error(
                EXECUTION_CONTEXT_NO_TASK,
                execution_id=self.execution_id,
                agent_id=str(self.identity.id),
                target_status=target.value,
            )
            raise ExecutionStateError(msg)
        try:
            new_execution = self.task_execution.with_transition(target, reason=reason)
        except ValueError:
            logger.warning(
                EXECUTION_CONTEXT_TRANSITION_FAILED,
                execution_id=self.execution_id,
                agent_id=str(self.identity.id),
                target_status=target.value,
                current_status=self.task_execution.status.value,
            )
            raise
        return self.model_copy(update={"task_execution": new_execution})

    def to_snapshot(self) -> AgentContextSnapshot:
        """Create a compact snapshot for reporting and logging.

        Returns:
            Frozen ``AgentContextSnapshot`` with current state.
        """
        te = self.task_execution
        snapshot = AgentContextSnapshot(
            execution_id=self.execution_id,
            agent_id=str(self.identity.id),
            task_id=te.task.id if te is not None else None,
            turn_count=self.turn_count,
            accumulated_cost=self.accumulated_cost,
            task_status=te.status if te is not None else None,
            started_at=self.started_at,
            snapshot_at=datetime.now(UTC),
            message_count=len(self.conversation),
        )
        logger.debug(
            EXECUTION_CONTEXT_SNAPSHOT,
            execution_id=self.execution_id,
        )
        return snapshot

    @property
    def has_turns_remaining(self) -> bool:
        """Whether the agent has turns remaining before hitting max_turns."""
        return self.turn_count < self.max_turns
