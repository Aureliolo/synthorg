"""Agent execution context.

Wraps an ``AgentIdentity`` (frozen config) with evolving runtime state
(conversation, cost, turn count, task execution) using
``model_copy(update=...)`` for cheap, immutable state transitions.
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.communication.async_tasks.models import (
    AsyncTaskStateChannel,
)
from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.compaction.models import CompressionMetadata  # noqa: TC001
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
        context_fill_tokens: Estimated tokens currently in the full
            context (system prompt + conversation + tool defs).
        context_capacity_tokens: Model's max context window tokens,
            or ``None`` when unknown.
        compression_metadata: Metadata about conversation compression,
            set when compaction has occurred.
        async_task_state: Dedicated state channel for tracked async
            tasks.  Separate from ``conversation`` -- not touched by
            compaction or context reset.
        loaded_tools: Tool names with L2 bodies active in context.
        loaded_resources: ``(tool_name, resource_id)`` pairs with
            L3 resources fetched.
        tool_load_order: Insertion-ordered tool names for FIFO
            auto-unload under budget pressure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
    context_fill_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated tokens in the full context",
    )
    context_capacity_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Model's max context window tokens",
    )
    compression_metadata: CompressionMetadata | None = Field(
        default=None,
        description="Compression metadata when compacted",
    )

    # ── Async task state channel ────────────────────────────────
    async_task_state: AsyncTaskStateChannel = Field(
        default_factory=AsyncTaskStateChannel,
        description=(
            "Async task tracking state (survives compaction and context reset)"
        ),
    )

    # ── Progressive tool disclosure state ─────────────────────────
    loaded_tools: frozenset[str] = Field(
        default=frozenset(),
        description="Tool names with L2 body active in context",
    )
    loaded_resources: frozenset[tuple[str, str]] = Field(
        default=frozenset(),
        description="(tool_name, resource_id) pairs with L3 active",
    )
    tool_load_order: tuple[str, ...] = Field(
        default=(),
        description="Insertion-ordered tool names for FIFO unload",
    )

    @model_validator(mode="after")
    def _validate_disclosure_consistency(self) -> AgentContext:
        """Ensure loaded_tools and tool_load_order are consistent."""
        order_set = set(self.tool_load_order)
        if order_set != self.loaded_tools:
            msg = (
                f"loaded_tools={self.loaded_tools} and "
                f"tool_load_order={self.tool_load_order} are inconsistent"
            )
            raise ValueError(msg)
        if len(self.tool_load_order) != len(order_set):
            msg = f"tool_load_order contains duplicates: {self.tool_load_order}"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Context fill percentage",
    )
    @property
    def context_fill_percent(self) -> float | None:
        """Percentage of context window currently filled.

        Returns ``None`` when context capacity is unknown.
        """
        if self.context_capacity_tokens is None:
            return None
        return (self.context_fill_tokens / self.context_capacity_tokens) * 100.0

    @classmethod
    def from_identity(
        cls,
        identity: AgentIdentity,
        *,
        task: Task | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        context_capacity_tokens: int | None = None,
    ) -> AgentContext:
        """Create a fresh execution context from an agent identity.

        Args:
            identity: The frozen agent identity card.
            task: Optional task to bind to this execution.
            max_turns: Maximum number of LLM turns allowed.
            context_capacity_tokens: Model's max context window
                tokens, or ``None`` when unknown.

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
            context_capacity_tokens=context_capacity_tokens,
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
            cost=usage.cost,
        )
        return result

    def with_context_fill(self, fill_tokens: int) -> AgentContext:
        """Update the estimated context fill level.

        Args:
            fill_tokens: New estimated fill in tokens.

        Returns:
            New ``AgentContext`` with updated fill level.

        Raises:
            ValueError: If ``fill_tokens`` is negative.
        """
        if fill_tokens < 0:
            msg = f"fill_tokens must be >= 0, got {fill_tokens}"
            raise ValueError(msg)
        return self.model_copy(
            update={"context_fill_tokens": fill_tokens},
        )

    def with_async_task_state(
        self,
        state: AsyncTaskStateChannel,
    ) -> AgentContext:
        """Replace the async task state channel.

        Args:
            state: New state channel.

        Returns:
            New ``AgentContext`` with updated state channel.
        """
        return self.model_copy(update={"async_task_state": state})

    def with_compression(
        self,
        metadata: CompressionMetadata,
        compressed_conversation: tuple[ChatMessage, ...],
        fill_tokens: int,
    ) -> AgentContext:
        """Replace conversation with a compressed version.

        Args:
            metadata: Compression metadata to attach.
            compressed_conversation: The compressed message tuple.
            fill_tokens: Updated fill estimate after compression.

        Returns:
            New ``AgentContext`` with compressed conversation.

        Raises:
            ValueError: If ``fill_tokens`` is negative.
        """
        if fill_tokens < 0:
            msg = f"fill_tokens must be >= 0, got {fill_tokens}"
            raise ValueError(msg)
        return self.model_copy(
            update={
                "conversation": compressed_conversation,
                "compression_metadata": metadata,
                "context_fill_tokens": fill_tokens,
            },
        )

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
        task_execution = self.task_execution
        snapshot = AgentContextSnapshot(
            execution_id=self.execution_id,
            agent_id=str(self.identity.id),
            task_id=task_execution.task.id if task_execution is not None else None,
            turn_count=self.turn_count,
            accumulated_cost=self.accumulated_cost,
            task_status=task_execution.status if task_execution is not None else None,
            started_at=self.started_at,
            snapshot_at=datetime.now(UTC),
            message_count=len(self.conversation),
            context_fill_tokens=self.context_fill_tokens,
            context_fill_percent=self.context_fill_percent,
        )
        logger.debug(
            EXECUTION_CONTEXT_SNAPSHOT,
            execution_id=self.execution_id,
        )
        return snapshot

    # ── Progressive disclosure state transitions ────────────────

    def with_tool_loaded(self, tool_name: str) -> AgentContext:
        """Mark a tool's L2 body as loaded.

        Idempotent: loading an already-loaded tool is a no-op.

        Args:
            tool_name: Name of the tool to load.

        Returns:
            New ``AgentContext`` with the tool marked as loaded.
        """
        if tool_name in self.loaded_tools:
            return self
        new_loaded = self.loaded_tools | {tool_name}
        new_order = (*self.tool_load_order, tool_name)
        return self.model_copy(
            update={
                "loaded_tools": new_loaded,
                "tool_load_order": new_order,
            },
        )

    def with_tool_unloaded(self, tool_name: str) -> AgentContext:
        """Mark a tool's L2 body as unloaded.

        Also removes any L3 resources for the unloaded tool.
        Idempotent: unloading an already-unloaded tool is a no-op.

        Args:
            tool_name: Name of the tool to unload.

        Returns:
            New ``AgentContext`` with the tool removed.
        """
        if tool_name not in self.loaded_tools:
            return self
        new_loaded = self.loaded_tools - {tool_name}
        new_order = tuple(t for t in self.tool_load_order if t != tool_name)
        new_resources = frozenset(
            (t, r) for t, r in self.loaded_resources if t != tool_name
        )
        return self.model_copy(
            update={
                "loaded_tools": new_loaded,
                "tool_load_order": new_order,
                "loaded_resources": new_resources,
            },
        )

    def with_resource_loaded(
        self,
        tool_name: str,
        resource_id: str,
    ) -> AgentContext:
        """Mark an L3 resource as fetched.

        Idempotent: loading an already-loaded resource is a no-op.

        Args:
            tool_name: Name of the tool owning the resource.
            resource_id: Identifier of the resource.

        Returns:
            New ``AgentContext`` with the resource marked as loaded.
        """
        pair = (tool_name, resource_id)
        if pair in self.loaded_resources:
            return self
        new_resources = self.loaded_resources | {pair}
        return self.model_copy(
            update={"loaded_resources": new_resources},
        )

    @property
    def has_turns_remaining(self) -> bool:
        """Whether the agent has turns remaining before hitting max_turns."""
        return self.turn_count < self.max_turns
