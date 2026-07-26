# module-kind: code
"""Shared run objects for the two plan-based execution loops.

``HybridLoop`` and ``PlanExecuteLoop`` mirror each other and drive the same
step-execution and replanning machinery. Both used to hand-thread the same
eleven-to-fifteen value bundle through every helper, which made the two
modules drift (the same parameter names sat in different positions) and put
two same-typed model ids next to each other at every call site.

:class:`StepRunContext` carries the half that is fixed for one ``execute()``
call; :class:`StepRunState` carries the half that moves as the loop walks the
plan. Splitting them that way is what lets the helpers return a bare verdict
instead of a tuple of rebound values.
"""

from dataclasses import dataclass, field
from enum import Enum

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
    """Non-terminal verdict from a single mini-ReAct turn.

    A step turn has three outcomes: terminate the run, continue the
    sub-loop, or finish the step with a success flag. The last two would
    collide if "continue" were spelled ``None``, because ``None`` and
    ``False`` share the falsy bucket and a ``if not outcome:`` check would
    silently treat a continuing turn as a failed step.
    """

    CONTINUE = "continue"


@dataclass(frozen=True, slots=True, kw_only=True)
class StepRunContext:
    """Collaborators and settings fixed for one ``execute()`` call.

    Keyword-only by construction: ``executor_model`` and ``planner_model``
    are both plain strings, so a positional constructor would let a caller
    transpose the two without any type error, which is the exact hazard the
    old fifteen-positional-argument call sites carried.

    ``completion_config`` is deliberately not named ``config``: the loops
    also carry a ``HybridLoopConfig`` / ``PlanExecuteConfig`` on ``self``,
    and the two used to share that name across the method and free-function
    variants of the same helper.
    """

    provider: CompletionProvider
    executor_model: str
    planner_model: str
    completion_config: CompletionConfig
    tool_invoker: ToolInvokerProtocol | None = None
    budget_checker: BudgetChecker | None = None
    shutdown_checker: ShutdownChecker | None = None
    task_cancellation_checker: TaskCancellationChecker | None = None
    turn_observer: TurnObserver | None = None
    checkpoint_callback: CheckpointCallback | None = None
    streaming_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject a blank model id at construction.

        Raises:
            ValueError: When either model id is blank, so the fail point is
                construction rather than the provider call several turns in.
        """
        if not self.executor_model.strip():
            msg = "StepRunContext.executor_model must be non-blank"
            raise ValueError(msg)
        if not self.planner_model.strip():
            msg = "StepRunContext.planner_model must be non-blank"
            raise ValueError(msg)


@dataclass(slots=True, kw_only=True)
class StepRunState:
    """Mutable cursor and accumulators for one walk of a plan.

    ``ctx`` and ``plan`` are frozen values rebound as the loop advances:
    assigning ``state.ctx = state.ctx.with_message(...)`` still evolves the
    context through ``model_copy``, so every ``AgentContext`` value stays
    immutable and only the binding moves.

    ``turns`` and ``all_plans`` are append-only accumulators whose identity
    is stable for the whole run. The planning phase builds them before the
    plan exists and hands them here, so the turns the planner recorded and
    the turns the step loop records are one list.
    """

    ctx: AgentContext
    plan: ExecutionPlan
    turns: list[TurnRecord] = field(default_factory=list)
    all_plans: list[ExecutionPlan] = field(default_factory=list)
    step_idx: int = 0
    replans_used: int = 0
