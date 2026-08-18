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
from typing import NamedTuple, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.session_budget import (
    SessionCeilings,
    build_session_budget_checker,
)
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.quality.models import StepQualitySignal
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
    NO_OP = "no_op"
    """A task-backed run that finished without calling any tool, so it
    produced no artifacts. A silent no-op success is a failure: the run
    is routed to ``FAILED`` unless an explicit no-op justification was
    recorded (see ``engine.task_sync``)."""


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    context: AgentContext = Field(description="Final agent context")
    termination_reason: TerminationReason = Field(
        description="Why the loop stopped",
    )
    turns: tuple[TurnRecord, ...] = Field(
        default=(),
        description="Per-turn metadata",
    )
    quality_signals: tuple[StepQualitySignal, ...] = Field(
        default=(),
        description="Per-step quality signals produced during the loop",
    )
    error_message: str | None = Field(
        default=None,
        description="Error description (when reason is ERROR)",
    )
    error_type: str | None = Field(
        default=None,
        description="Class name of the exception that terminated the run",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Forward-compatible metadata for future loop types",
    )

    @computed_field(
        description="Total tool calls across all turns",
    )
    @property
    def total_tool_calls(self) -> int:
        """Sum of tool calls from all turn records."""
        return sum(len(t.tool_calls_made) for t in self.turns)

    @model_validator(mode="before")
    @classmethod
    def _deep_copy_metadata(cls, data: object) -> object:
        """Deep-copy the supplied metadata dict at the construction boundary.

        Returns:
            The input with ``metadata`` replaced by a deep copy, so a
            later mutation of the caller's dict cannot reach into this
            frozen record's nested metadata.
        """
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            return {**data, "metadata": copy.deepcopy(data["metadata"])}
        return data

    @model_validator(mode="after")
    def _validate_error_message(self) -> Self:
        if self.termination_reason in (
            TerminationReason.ERROR,
            TerminationReason.NO_OP,
        ):
            if self.error_message is None:
                msg = (
                    "error_message is required when termination_reason is "
                    f"{self.termination_reason.value}"
                )
                raise ValueError(msg)
        elif self.termination_reason == TerminationReason.PARKED:
            if self.error_message is not None:
                msg = "error_message must be None for PARKED termination"
                raise ValueError(msg)
        elif self.error_message is not None:
            msg = "error_message must be None when termination_reason is not ERROR"
            raise ValueError(msg)
        return self


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


class TurnProgress(NamedTuple):
    """What a loop reports about one turn while the run is still going.

    The ``context`` is carried because everything an operator wants to know
    about a run in flight (how many turns, how much spend, when it last did
    anything) lives on it and nowhere else until the run finishes, so a
    report without it can say only that a turn happened.

    The context carries the run's whole conversation, which is
    agent-authored and holds tool results from outside the system. It is
    fenced where it is STORED, not here, so an observer that puts any of it
    into a prompt (a narration call, a summary, an LLM-scored dashboard)
    owes it a ``wrap_untrusted`` at that boundary, exactly as the review
    gate's own inputs do. The observers shipped today read scalars only
    (turn count, spend, timestamps, tool names), so none of them needs one.

    Attributes:
        turn_number: 1-based index of the turn just observed.
        tool_names: Short labels for what that turn did.
        context: The run's context as it stands after the turn. Untrusted
            content: see above before putting any of it in a prompt.
    """

    turn_number: int
    tool_names: tuple[str, ...]
    context: AgentContext


TurnObserver = Callable[[TurnProgress], Awaitable[None]]
"""Async progress callback invoked with a :class:`TurnProgress`. Two calling
conventions share this shape:

- ReAct loop: fires *after* each continuing turn with the tool names that turn
  requested; the terminal turn (which ends the loop) returns before the hook,
  so no observation marks it.
- OpenHands loop: fires as each event arrives off the harness stream, with a
  one-element tuple naming the tool the event used, or empty when the event
  named none.

Purely observational: it never affects control flow, and an observer raising
must not corrupt the run. Used to surface incremental progress on a streamed
chat action and to keep the live-activity state current; ``None`` disables
it."""


@runtime_checkable
class ExecutionLoop(Protocol):
    """Protocol for agent execution loops.

    The agent engine calls ``execute`` to run a task through the loop.
    Implementations decide the control flow but all return an
    ``ExecutionResult`` with a ``TerminationReason``.
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
        turn_observer: TurnObserver | None = None,
        streaming_enabled: bool = False,
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
            turn_observer: Optional per-run progress callback; used to
                project live execution progress onto the AG-UI stream and to
                keep the live-activity state current. Awaited once per turn
                with a single :class:`TurnProgress`.
            streaming_enabled: When ``True``, each per-turn LLM call streams
                and is interruptible mid-flight (operator cancellation and
                steering REDIRECT); otherwise a non-streaming call is used.

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
    """Create a budget checker if the task carries either bound.

    The returned callable returns ``True`` when accumulated cost meets the
    task's money limit OR accumulated tokens meet its token ceiling. The
    token half matters because money measures nothing against a provider
    that bills by flat subscription, where the cost bound can never fire.

    Returns:
        A :class:`BudgetChecker` closure over ``task.budget_limit`` and
        ``task.hard_token_ceiling``; ``None`` when the task carries neither.
    """
    return build_session_budget_checker(
        SessionCeilings.of(
            cost_ceiling=task.budget_limit,
            token_ceiling=task.hard_token_ceiling,
        )
    )
