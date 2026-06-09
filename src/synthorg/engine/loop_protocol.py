"""Execution loop protocol and supporting models.

Defines the ``ExecutionLoop`` protocol that the agent engine calls to
run a task, along with ``ExecutionResult``, ``TerminationReason``, and the
``BudgetChecker`` and ``ShutdownChecker`` type aliases. ``TurnRecord`` is
imported from ``synthorg.execution.turn`` (the engine-free leaf) and
re-exported here for callers.
"""

import copy
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.execution.turn import TurnRecord
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol


class TerminationReason(StrEnum):
    """Why the execution loop terminated."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SHUTDOWN = "shutdown"
    PARKED = "parked"
    STAGNATION = "stagnation"
    CANCELLED = "cancelled"
    ERROR = "error"


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

TaskCancellationChecker = Callable[[], Awaitable[bool]]
"""Async callback that returns ``True`` when the running task has been cancelled
or superseded externally (e.g. by a steering supersession or a cockpit kill).

Consulted at the top-of-turn safe boundary so the agent halts cleanly instead of
running an obsolete task to completion. The task's terminal DB status is the
durable cross-process signal (the operator cancels in the API process; the agent
runs in the worker process)."""


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
        task_cancellation_checker: TaskCancellationChecker | None = None,
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
            task_cancellation_checker: Optional async callback; returns
                ``True`` when the running task was cancelled or superseded
                externally, so the loop halts at the next safe boundary.

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
