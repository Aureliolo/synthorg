"""Execution loop protocol and supporting models.

Defines the ``ExecutionLoop`` protocol that the agent engine calls to
run a task, along with ``ExecutionResult``, ``TurnRecord``,
``TerminationReason``, and the ``BudgetChecker`` and ``ShutdownChecker``
type aliases.
"""

import copy
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.call_category import LLMCallCategory  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.context import AgentContext
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.engine.trajectory.efficiency_ratios import EfficiencyRatios
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.protocol import ToolInvokerProtocol


class NodeType(StrEnum):
    """Type of computation node executed within a turn.

    Used for structural credit assignment and post-hoc trace analysis.
    Each turn records which node types executed, enabling fine-grained
    attribution of costs and failures.
    """

    LLM_CALL = "llm_call"
    TOOL_INVOCATION = "tool_invocation"
    QUALITY_CHECK = "quality_check"
    BUDGET_CHECK = "budget_check"
    STAGNATION_CHECK = "stagnation_check"


class BehaviorTag(StrEnum):
    """Behavior category for trace capture and eval routing.

    Starting taxonomy derived from agent evaluation patterns.
    Extend as usage patterns reveal category fragmentation or
    generalization.
    """

    FILE_OPERATIONS = "file_operations"
    RETRIEVAL = "retrieval"
    TOOL_USE = "tool_use"
    MEMORY = "memory"
    CONVERSATION = "conversation"
    SUMMARIZATION = "summarization"
    DELEGATION = "delegation"
    COORDINATION = "coordination"
    VERIFICATION = "verification"


class TerminationReason(StrEnum):
    """Why the execution loop terminated."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SHUTDOWN = "shutdown"
    PARKED = "parked"
    STAGNATION = "stagnation"
    ERROR = "error"


class TurnRecord(BaseModel):
    """Per-turn metadata recorded during execution.

    Attributes:
        turn_number: 1-indexed turn number.
        input_tokens: Input tokens consumed this turn.
        output_tokens: Output tokens generated this turn.
        total_tokens: Sum of input and output tokens (computed).
        cost: Cost in the configured currency for this turn.
        tool_calls_made: Names of tools invoked this turn.
        tool_call_fingerprints: Deterministic fingerprints of tool
            calls (``name:args_hash``) for stagnation detection.
        finish_reason: LLM finish reason for this turn.
        call_category: Optional LLM call category for coordination
            metrics (productive, coordination, system).
        latency_ms: Round-trip latency in milliseconds (``None`` if not measured).
        cache_hit: Whether the provider served this turn from cache.
        retry_count: Number of retry attempts before success.
        retry_reason: Exception type name of the last retried error.
        node_types: Node types that executed in this turn (e.g.
            LLM_CALL, TOOL_INVOCATION). Defaults to empty for
            deserialization of legacy data.
        behavior_tags: Behavior categories inferred by BehaviorTaggerMiddleware.
        efficiency_delta: Efficiency ratios against an ideal baseline.
        prior_tool_call_count: Cumulative tool calls before this turn (for PTE).
        tool_response_tokens: Tokens from tool responses this turn (for PTE).
        semantic_drift_score: Similarity score (0.0--1.0) from
            SemanticDriftDetector, or ``None`` if not measured.
        success: Whether this turn completed without error or content filter (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    turn_number: int = Field(gt=0, description="1-indexed turn number")
    input_tokens: int = Field(ge=0, description="Input tokens this turn")
    output_tokens: int = Field(ge=0, description="Output tokens this turn")
    cost: float = Field(ge=0.0, description="Cost in the configured currency this turn")
    tool_calls_made: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tool names invoked this turn",
    )
    tool_call_fingerprints: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deterministic fingerprints of tool calls (name:args_hash)",
    )
    finish_reason: FinishReason = Field(
        description="LLM finish reason this turn",
    )
    call_category: LLMCallCategory | None = Field(
        default=None,
        description="LLM call category (productive, coordination, system)",
    )
    latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Round-trip latency in milliseconds from provider base class",
    )
    cache_hit: bool | None = Field(
        default=None,
        description="Whether the provider served this turn from cache",
    )
    retry_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of retry attempts before success",
    )
    retry_reason: NotBlankStr | None = Field(
        default=None,
        description="Exception type name of the last retried error",
    )
    node_types: tuple[NodeType, ...] = Field(
        default=(),
        description="Node types that executed in this turn",
    )
    behavior_tags: tuple[BehaviorTag, ...] = Field(
        default=(),
        description="Behavior categories inferred by BehaviorTaggerMiddleware",
    )
    efficiency_delta: EfficiencyRatios | None = Field(
        default=None,
        description="Efficiency ratios against an ideal baseline",
    )
    prior_tool_call_count: int = Field(
        default=0,
        ge=0,
        description="Cumulative tool calls before this turn (for PTE)",
    )
    tool_response_tokens: int = Field(
        default=0,
        ge=0,
        description="Tokens from tool responses this turn (for PTE)",
    )
    semantic_drift_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Semantic drift similarity score (from SemanticDriftDetector)",
    )

    @model_validator(mode="after")
    def _validate_retry_consistency(self) -> Self:
        """Ensure retry_reason implies retry_count >= 1.

        Returns:
            ``self`` unchanged when retry fields are consistent.

        Raises:
            ValueError: When ``retry_reason`` is set without a
                non-zero ``retry_count``.
        """
        if self.retry_reason is not None and (
            self.retry_count is None or self.retry_count == 0
        ):
            msg = "retry_reason set implies retry_count must be >= 1"
            raise ValueError(msg)
        return self

    @computed_field(description="Total token count")  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether this turn completed without error or content filter",
    )
    @property
    def success(self) -> bool:
        """True unless finish_reason is ERROR or CONTENT_FILTER."""
        return self.finish_reason not in (
            FinishReason.ERROR,
            FinishReason.CONTENT_FILTER,
        )


class ExecutionResult(BaseModel):
    """Result returned by an execution loop.

    Attributes:
        context: Final agent context after execution.
        termination_reason: Why the loop stopped.
        turns: Per-turn metadata records.
        total_tool_calls: Total tool calls across all turns (computed).
        error_message: Error description when termination_reason is ERROR.
        metadata: Forward-compatible dict for future loop types.
            Note: ``frozen=True`` prevents field reassignment but not
            in-place mutation of the dict contents; deep-copy at
            system boundaries per project conventions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    context: AgentContext = Field(description="Final agent context")
    termination_reason: TerminationReason = Field(
        description="Why the loop stopped",
    )
    turns: tuple[TurnRecord, ...] = Field(
        default=(),
        description="Per-turn metadata",
    )
    error_message: str | None = Field(
        default=None,
        description="Error description (when reason is ERROR)",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Forward-compatible metadata for future loop types",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Total tool calls across all turns",
    )
    @property
    def total_tool_calls(self) -> int:
        """Sum of tool calls from all turn records."""
        return sum(len(t.tool_calls_made) for t in self.turns)

    @model_validator(mode="after")
    def _validate_error_message(self) -> Self:
        if self.termination_reason == TerminationReason.ERROR:
            if self.error_message is None:
                msg = "error_message is required when termination_reason is ERROR"
                raise ValueError(msg)
        elif self.termination_reason == TerminationReason.PARKED:
            if self.error_message is not None:
                msg = "error_message must be None for PARKED termination"
                raise ValueError(msg)
        elif self.error_message is not None:
            msg = "error_message must be None when termination_reason is not ERROR"
            raise ValueError(msg)
        return self

    def __init__(self, **data: object) -> None:
        """Deep-copy metadata dict at construction boundary."""
        if "metadata" in data and isinstance(data["metadata"], dict):
            data["metadata"] = copy.deepcopy(data["metadata"])
        super().__init__(**data)


BudgetChecker = Callable[[AgentContext], bool]
"""Callback that returns ``True`` when the budget is exhausted."""

ShutdownChecker = Callable[[], bool]
"""Callback that returns ``True`` when a graceful shutdown has been requested."""


@runtime_checkable
class ExecutionLoop(Protocol):
    """Protocol for agent execution loops.

    The agent engine calls ``execute`` to run a task through the loop.
    Implementations decide the control flow (ReAct, Plan-and-Execute, etc.)
    but all return an ``ExecutionResult`` with a ``TerminationReason``.
    """

    async def execute(  # noqa: PLR0913
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvokerProtocol | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
    ) -> ExecutionResult:
        """Run the execution loop.

        Args:
            context: Initial agent context with conversation and identity.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional callback; returns ``True`` when
                budget is exhausted.
            shutdown_checker: Optional callback; returns ``True`` when
                a graceful shutdown has been requested.
            completion_config: Optional per-execution override for
                temperature/max_tokens (defaults to identity's model config).

        Returns:
            Execution result with final context and termination reason.
        """
        ...

    def get_loop_type(self) -> str:
        """Return the loop type identifier (e.g. ``"react"``).

        Returns:
            The loop's type discriminator string.
        """
        ...


def make_budget_checker(task: Task) -> BudgetChecker | None:
    """Create a budget checker if the task has a positive budget limit.

    The returned callable returns ``True`` when accumulated cost meets
    or exceeds the limit (budget exhausted), ``False`` otherwise.
    Returns ``None`` when there is no positive budget limit.

    Returns:
        A :class:`BudgetChecker` closure over ``task.budget_limit``;
        ``None`` when the task has no positive budget.
    """
    if task.budget_limit <= 0:
        return None

    limit = task.budget_limit

    def _check(ctx: AgentContext) -> bool:
        return ctx.accumulated_cost.cost >= limit

    return _check
