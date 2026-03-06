"""Execution loop protocol and supporting models.

Defines the ``ExecutionLoop`` protocol that the agent engine calls to
run a task, along with ``ExecutionResult``, ``TurnRecord``,
``TerminationReason``, and ``BudgetChecker``.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ai_company.engine.context import AgentContext
from ai_company.providers.enums import FinishReason  # noqa: TC001

if TYPE_CHECKING:
    from ai_company.providers.models import CompletionConfig
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.tools.invoker import ToolInvoker


class TerminationReason(StrEnum):
    """Why the execution loop terminated."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ERROR = "error"


class TurnRecord(BaseModel):
    """Per-turn metadata recorded during execution.

    Attributes:
        turn_number: 1-indexed turn number.
        input_tokens: Input tokens consumed this turn.
        output_tokens: Output tokens generated this turn.
        cost_usd: Cost in USD for this turn.
        tool_calls_made: Names of tools invoked this turn.
        finish_reason: LLM finish reason for this turn.
    """

    model_config = ConfigDict(frozen=True)

    turn_number: int = Field(gt=0, description="1-indexed turn number")
    input_tokens: int = Field(ge=0, description="Input tokens this turn")
    output_tokens: int = Field(ge=0, description="Output tokens this turn")
    cost_usd: float = Field(ge=0.0, description="Cost in USD this turn")
    tool_calls_made: tuple[str, ...] = Field(
        default=(),
        description="Tool names invoked this turn",
    )
    finish_reason: FinishReason = Field(
        description="LLM finish reason this turn",
    )


class ExecutionResult(BaseModel):
    """Result returned by an execution loop.

    Attributes:
        context: Final agent context after execution.
        termination_reason: Why the loop stopped.
        turns: Per-turn metadata records.
        total_tool_calls: Total number of tool calls across all turns.
        error_message: Error description when termination_reason is ERROR.
        metadata: Forward-compatible dict for future loop types.
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
    total_tool_calls: int = Field(
        ge=0,
        description="Total tool calls across all turns",
    )
    error_message: str | None = Field(
        default=None,
        description="Error description (when reason is ERROR)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Forward-compatible metadata for future loop types",
    )


BudgetChecker = Callable[[AgentContext], bool]
"""Callback that returns ``True`` when the budget is exhausted."""


@runtime_checkable
class ExecutionLoop(Protocol):
    """Protocol for agent execution loops.

    The agent engine calls ``execute`` to run a task through the loop.
    Implementations decide the control flow (ReAct, Plan-and-Execute, etc.)
    but all return an ``ExecutionResult`` with a ``TerminationReason``.
    """

    async def execute(
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvoker | None = None,
        budget_checker: BudgetChecker | None = None,
        completion_config: CompletionConfig | None = None,
    ) -> ExecutionResult:
        """Run the execution loop.

        Args:
            context: Initial agent context with conversation and identity.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional callback; returns ``True`` when
                budget is exhausted.
            completion_config: Optional per-execution override for
                temperature/max_tokens (defaults to identity's model config).

        Returns:
            Execution result with final context and termination reason.
        """
        ...

    def get_loop_type(self) -> str:
        """Return the loop type identifier (e.g. ``"react"``)."""
        ...
