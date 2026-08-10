"""Agent execution context.

Wraps an ``AgentIdentity`` (frozen config) with evolving runtime state
(conversation, cost, turn count, task execution) using
``model_copy(update=...)`` for cheap, immutable state transitions.
"""

from datetime import UTC, datetime
from typing import Final
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
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.compaction.models import CompressionMetadata
from synthorg.engine.context_disclosure import (
    resource_loaded_update,
    tool_loaded_update,
    tool_unloaded_update,
    validate_tool_disclosure,
)
from synthorg.engine.context_snapshot import AgentContextSnapshot
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

DEFAULT_MAX_TURNS: Final[int] = 300
"""Default hard limit on LLM turns per agent execution.

A backstop against a pathological loop, not a work budget. What actually
bounds an ordinary run is its cost ceiling, its stagnation detector and its
stage timeout, each of which stops a run that is spending without
progressing. The turn cap only has to sit above what real work takes.

Twenty is a chat-assistant number: a build agent spends that reading the
code before it edits anything, so it ran out mid-build with real files
written and the run was discarded.

Fallback when ``engine.max_turns`` is not resolvable; the operator-tunable
value flows through that setting (see ``AgentEngine._resolve_max_turns``)."""

DEFAULT_MAX_TURN_EXTENSIONS: Final[int] = 3
"""How many further turn budgets a run may grant itself before it parks.

Reaching the cap usually means the work was bigger than the estimate, not
that anything is wrong, so the common case is answered by carrying on rather
than by interrupting a human. Bounded, because a run that has taken four
full budgets is no longer a long task, it is a question; at that point the
run parks with its workspace intact and asks whether to continue.

Zero restores the old behaviour: the first ceiling ends the run."""


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
    turn_extensions_remaining: int = Field(
        default=0,
        ge=0,
        description="Further turn budgets this run may grant itself",
    )
    turn_extensions_granted: int = Field(
        default=0,
        ge=0,
        description="Further turn budgets this run has already taken",
    )
    cost_ceiling: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional per-session cost ceiling; the chat-action loop halts "
            "once accumulated cost meets it. Carried on the context so the "
            "bound survives a park/resume round-trip."
        ),
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

    # ── Mid-flight steering adoption state ────────────────────────
    adopted_steering_ids: frozenset[NotBlankStr] = Field(
        default=frozenset(),
        description="Steering directive entry ids already adopted by this run",
    )

    @model_validator(mode="after")
    def _validate_disclosure_consistency(self) -> AgentContext:
        """Ensure loaded_tools and tool_load_order are consistent.

        Returns:
            ``self`` unchanged when the tool disclosure state is
            internally consistent.

        Raises:
            ValueError: When ``loaded_tools`` does not match the set
                of names in ``tool_load_order``, or when
                ``tool_load_order`` carries duplicates.
        """
        validate_tool_disclosure(self.loaded_tools, self.tool_load_order)
        return self

    @computed_field(
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
        turn_extensions: int = 0,
        context_capacity_tokens: int | None = None,
        cost_ceiling: float | None = None,
    ) -> AgentContext:
        """Create a fresh execution context from an agent identity.

        Args:
            identity: The frozen agent identity card.
            task: Optional task to bind to this execution.
            max_turns: Maximum number of LLM turns allowed.
            turn_extensions: How many further turn budgets the run may grant
                itself before parking for a human. Zero, the default, ends the
                run at the first ceiling: extensions are task-run policy, and
                a bounded session (decomposition, a review panellist, a chat
                action) sets its own cap and never asked to exceed it. Only
                the task-run path passes the operator's configured value.
            context_capacity_tokens: Model's max context window
                tokens, or ``None`` when unknown.
            cost_ceiling: Optional per-session cost ceiling. Passed through
                the constructor (not a post-hoc ``model_copy``) so the
                ``gt=0`` / no-NaN field constraint actually validates it.

        Returns:
            New ``AgentContext`` ready for execution.
        """
        task_execution = TaskExecution.from_task(task) if task is not None else None
        context = cls(
            execution_id=str(uuid4()),
            identity=identity,
            task_execution=task_execution,
            max_turns=max_turns,
            turn_extensions_remaining=turn_extensions,
            started_at=datetime.now(UTC),
            context_capacity_tokens=context_capacity_tokens,
            cost_ceiling=cost_ceiling,
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

    def with_steering_adopted(self, directive_id: NotBlankStr) -> AgentContext:
        """Mark a mid-flight steering directive as adopted by this run.

        Adoption is context-local and travels with the checkpointed
        context, so every concurrent agent on a project adopts a
        directive independently and a resumed run never re-adopts one it
        already consumed. Idempotent: re-adopting is a no-op.

        Args:
            directive_id: The project-brain entry id of the directive.

        Returns:
            New ``AgentContext`` with the directive id recorded; the same
            instance when it was already adopted.
        """
        if directive_id in self.adopted_steering_ids:
            return self
        return self.model_copy(
            update={
                "adopted_steering_ids": self.adopted_steering_ids | {directive_id},
            },
        )

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
            task_id=str(task_execution.task.id) if task_execution is not None else None,
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
        update = tool_loaded_update(self.loaded_tools, self.tool_load_order, tool_name)
        return self if update is None else self.model_copy(update=update)

    def with_tool_unloaded(self, tool_name: str) -> AgentContext:
        """Mark a tool's L2 body as unloaded.

        Also removes any L3 resources for the unloaded tool.
        Idempotent: unloading an already-unloaded tool is a no-op.

        Args:
            tool_name: Name of the tool to unload.

        Returns:
            New ``AgentContext`` with the tool removed.
        """
        update = tool_unloaded_update(
            self.loaded_tools, self.tool_load_order, self.loaded_resources, tool_name
        )
        return self if update is None else self.model_copy(update=update)

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
        update = resource_loaded_update(self.loaded_resources, tool_name, resource_id)
        return self if update is None else self.model_copy(update=update)

    @property
    def has_turns_remaining(self) -> bool:
        """Whether the agent has turns remaining before hitting max_turns."""
        return self.turn_count < self.max_turns
