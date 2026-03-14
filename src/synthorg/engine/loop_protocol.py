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
from synthorg.providers.enums import FinishReason  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.invoker import ToolInvoker


class TerminationReason(StrEnum):
    """Why the execution loop terminated."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SHUTDOWN = "shutdown"
    PARKED = "parked"
    ERROR = "error"


class TurnRecord(BaseModel):
    """Per-turn metadata recorded during execution.

    Attributes:
        turn_number: 1-indexed turn number.
        input_tokens: Input tokens consumed this turn.
        output_tokens: Output tokens generated this turn.
        total_tokens: Sum of input and output tokens (computed).
        cost_usd: Cost in USD for this turn.
        tool_calls_made: Names of tools invoked this turn.
        finish_reason: LLM finish reason for this turn.
        call_category: Optional LLM call category for coordination
            metrics (productive, coordination, system).
    """

    model_config = ConfigDict(frozen=True)

    turn_number: int = Field(gt=0, description="1-indexed turn number")
    input_tokens: int = Field(ge=0, description="Input tokens this turn")
    output_tokens: int = Field(ge=0, description="Output tokens this turn")
    cost_usd: float = Field(ge=0.0, description="Cost in USD this turn")
    tool_calls_made: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tool names invoked this turn",
    )
    finish_reason: FinishReason = Field(
        description="LLM finish reason this turn",
    )
    call_category: LLMCallCategory | None = Field(
        default=None,
        description="LLM call category (productive, coordination, system)",
    )

    @computed_field(description="Total token count")  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


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

    model_config = ConfigDict(frozen=True)

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
        tool_invoker: ToolInvoker | None = None,
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
        """Return the loop type identifier (e.g. ``"react"``)."""
        ...


def make_budget_checker(task: Task) -> BudgetChecker | None:
    """Create a budget checker if the task has a positive budget limit.

    The returned callable returns ``True`` when accumulated cost meets
    or exceeds the limit (budget exhausted), ``False`` otherwise.
    Returns ``None`` when there is no positive budget limit.
    """
    if task.budget_limit <= 0:
        return None

    limit = task.budget_limit

    def _check(ctx: AgentContext) -> bool:
        return ctx.accumulated_cost.cost_usd >= limit

    return _check
