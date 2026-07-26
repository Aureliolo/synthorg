# module-kind: code
"""Shared run objects for the two plan-based execution loops.

``HybridLoop`` and ``PlanExecuteLoop`` drive the same step-execution and
replanning machinery, so they share one bundle of collaborators rather than
each naming its own.

:class:`StepRunContext` carries the half that is fixed for one ``execute()``
call; :class:`StepRunState` carries the half that moves as the loop walks the
plan. Splitting them that way is what lets the helpers return a bare verdict
instead of a tuple of rebound values.
"""

from dataclasses import dataclass, field
from enum import Enum

from synthorg.core.types import NotBlankStr, require_not_blank
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ShutdownChecker,
    TaskCancellationChecker,
    TurnObserver,
)
from synthorg.engine.plan_models import ExecutionPlan
from synthorg.execution.turn import TurnRecord
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol


class StepTurnOutcome(Enum):
    """Verdict from a single mini-ReAct turn that does not end the run.

    Spelling every arm as an enum member keeps them all out of the ``None``
    and ``False`` falsy bucket, so an ``if not outcome:`` check cannot read a
    continuing turn as a failed step, and lets the caller dispatch with an
    exhaustive ``match`` rather than an ``isinstance`` chain that a fourth
    arm could silently fall through.
    """

    CONTINUE = "continue"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"

    @classmethod
    def from_success(cls, *, success: bool) -> StepTurnOutcome:
        """Map a step's success flag onto the matching terminal member.

        Returns:
            ``STEP_SUCCEEDED`` when *success* is true, ``STEP_FAILED``
            otherwise.
        """
        return cls.STEP_SUCCEEDED if success else cls.STEP_FAILED

    @property
    def step_succeeded(self) -> bool:
        """Whether this outcome ends the step with a success.

        Returns:
            ``True`` only for ``STEP_SUCCEEDED``.
        """
        return self is StepTurnOutcome.STEP_SUCCEEDED


class ReplanVerdict(Enum):
    """Whether the loop should revise the plan or walk on to the next step.

    Named members rather than a ``bool`` for the same reason as
    :class:`StepTurnOutcome`: these values share a union with
    ``ExecutionResult``, which is always truthy, so a bare ``True`` / ``False``
    would make ``if verdict:`` read as "no result" for the one case that most
    needs handling.
    """

    REPLAN = "replan"
    PROCEED = "proceed"

    @classmethod
    def from_flag(cls, *, replan: bool) -> ReplanVerdict:
        """Map a decision flag onto the matching member.

        Returns:
            ``REPLAN`` when *replan* is true, ``PROCEED`` otherwise.
        """
        return cls.REPLAN if replan else cls.PROCEED

    @property
    def wants_replan(self) -> bool:
        """Whether this verdict calls for a revised plan.

        Returns:
            ``True`` only for ``REPLAN``.
        """
        return self is ReplanVerdict.REPLAN


class ReplanTrigger(Enum):
    """What prompted a revision of the current plan.

    Named on every replan log line so one event name carries one stable field
    set across both loops, and passed instead of a ``step_failed`` flag so the
    three call sites stay distinguishable where a bool would flatten two of
    them together.
    """

    STEP_FAILURE = "step_failure"
    COMPLETION_SUMMARY = "completion_summary"
    STEERING = "steering"

    @property
    def step_failed(self) -> bool:
        """Whether the step that triggered the replan had failed.

        Returns:
            ``True`` only for ``STEP_FAILURE``.
        """
        return self is ReplanTrigger.STEP_FAILURE


@dataclass(frozen=True, slots=True, kw_only=True)
class StepRunContext:
    """Collaborators and settings fixed for one ``execute()`` call.

    Keyword-only by construction: ``executor_model`` and ``planner_model``
    are both plain strings, so a positional constructor would let a caller
    transpose the two without any type error.

    ``completion_config`` is deliberately not named ``config``, because the
    loops also carry a ``HybridLoopConfig`` / ``PlanExecuteConfig`` on
    ``self`` and a bare ``config`` next to it reads ambiguously.

    ``provider`` and ``tool_invoker`` are held out of ``__repr__``: a driver
    caches resolved credentials, so an incidental repr of this object (an
    APM frame-locals capture, a verbose assertion message) must not be able
    to surface them.
    """

    provider: CompletionProvider = field(repr=False)
    executor_model: NotBlankStr
    planner_model: NotBlankStr
    completion_config: CompletionConfig
    tool_invoker: ToolInvokerProtocol | None = field(default=None, repr=False)
    budget_checker: BudgetChecker | None = None
    shutdown_checker: ShutdownChecker | None = None
    task_cancellation_checker: TaskCancellationChecker | None = None
    turn_observer: TurnObserver | None = None
    checkpoint_callback: CheckpointCallback | None = None
    streaming_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject a blank model id at construction.

        A ``NotBlankStr`` annotation only runs under Pydantic, so a plain
        dataclass has to enforce the contract itself or the fail point moves
        to the provider call several turns in.

        Raises:
            ValueError: When either model id is blank.
        """
        require_not_blank(self.executor_model, "executor_model")
        require_not_blank(self.planner_model, "planner_model")


@dataclass(slots=True, kw_only=True)
class StepRunState:
    """Mutable cursor and accumulators for one walk of a plan.

    One ``execute()`` call owns one instance for its whole lifetime. Nothing
    may store it on ``self`` or hand it to a second concurrently-scheduled
    coroutine: every mutation below is a read-modify-write with no ``await``
    between the read and the write, which is race-free only while a single
    task owns the object.

    ``ctx`` and ``plan`` are frozen values rebound as the loop advances, so
    assigning ``state.ctx = state.ctx.with_message(...)`` still evolves the
    context through ``model_copy`` and only the binding moves.

    ``turns`` is append-only and its identity is stable for the whole run:
    ``execute()`` builds it before a plan exists and hands it to the planning
    phase, so the turns the planner recorded and the turns the step loop
    records are one list. A helper handed ``turns`` alongside a by-value
    ``ctx`` mutates the shared list before its caller commits the matching
    ``ctx``, so the two must be read as one unit. ``all_plans`` grows by one
    entry per adopted plan, and its last entry is resynced in place through
    :meth:`sync_current_plan` as the live plan's step statuses change.
    """

    ctx: AgentContext
    plan: ExecutionPlan
    turns: list[TurnRecord]
    all_plans: list[ExecutionPlan]
    step_idx: int = 0
    replans_used: int = 0

    def advance_step(self) -> None:
        """Move the cursor to the next step of the current plan."""
        self.step_idx += 1

    def restart_plan(self) -> None:
        """Point the cursor back at the first step of the current plan."""
        self.step_idx = 0

    def record_replan(
        self,
        plan: ExecutionPlan,
        *,
        counts_against_budget: bool = True,
    ) -> None:
        """Adopt a revised plan and append it to the run history.

        Keeping the three writes together is what stops ``all_plans`` and
        ``replans_used`` drifting apart across the call sites that replan.

        Args:
            plan: The revised plan to adopt as current.
            counts_against_budget: Whether this replan consumes one of the
                run's ``max_replans``. An operator steering directive is
                exempt from that budget and passes ``False``.
        """
        self.plan = plan
        self.all_plans.append(plan)
        if counts_against_budget:
            self.replans_used += 1

    def sync_current_plan(self) -> None:
        """Write the live plan's step statuses onto the last history entry."""
        if self.all_plans:
            self.all_plans[-1] = self.plan
