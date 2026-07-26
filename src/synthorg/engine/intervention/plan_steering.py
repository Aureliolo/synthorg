"""Steering-driven replan for the Plan/Hybrid loops.

When a REDIRECT directive is adopted mid-step, the loop records a pending replan
on the context; at the next step boundary it forces a plan revision so the
remaining work honours the directive. This lives outside the loop modules (which
are at their size budgets) as a free function that receives the loop's planner
and finalize callables, keeping it decoupled from the loop class internals.
"""

from typing import Protocol

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.plan_loop_context import StepRunContext, StepRunState
from synthorg.engine.plan_models import ExecutionPlan, StepStatus
from synthorg.engine.plan_parsing import _REPLAN_JSON_EXAMPLE
from synthorg.engine.prompt_safety import (
    TAG_BRAIN_STATE,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_START,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)


class _PlannerCall(Protocol):
    """The loop's ``_call_planner`` bound method."""

    async def __call__(
        self,
        run: StepRunContext,
        ctx: AgentContext,
        turns: list[TurnRecord],
        message: ChatMessage,
        *,
        revision_number: int = 0,
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult: ...


class _Finalize(Protocol):
    """The loop's ``_finalize`` callable that attaches plan metadata."""

    def __call__(
        self,
        result: ExecutionResult,
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult: ...


async def steering_replan(
    run: StepRunContext,
    state: StepRunState,
    *,
    call_planner: _PlannerCall,
    finalize: _Finalize,
) -> ExecutionResult | None:
    """Replan after adopting a mid-flight steering REDIRECT.

    The directive is already in ``state.ctx`` (injected at the turn
    boundary); this revises the plan for the remaining work to honour it.
    Unlike a failure replan it does not count against ``max_replans``
    because it is operator-driven and consume-once (the directive id is
    cleared here).

    Returns:
        ``None`` once the revised plan is adopted onto ``state`` with the
        pending-replan flag cleared, or a terminal
        :class:`ExecutionResult`.
    """
    plan = state.plan
    logger.info(
        EXECUTION_PLAN_REPLAN_START,
        execution_id=state.ctx.execution_id,
        trigger="steering",
        directive_id=state.ctx.pending_steering_replan_id,
        revision=plan.revision_number,
    )
    completed_summary = (
        "\n".join(
            f"  Step {s.step_number}: {s.description} -> COMPLETED"
            for s in plan.steps
            if s.status == StepStatus.COMPLETED
        )
        or "  (none)"
    )
    # Step descriptions are agent-generated and may have absorbed external
    # tool output, so fence them before they re-enter the planner prompt.
    replan_content = (
        "One or more operator steering directives were just adopted (see the "
        "latest USER message(s) above). Create a revised plan for the REMAINING "
        "work that honours every adopted directive and abandons any step they "
        "make obsolete.\n\n"
        "Completed steps so far:\n"
        f"{wrap_untrusted(TAG_BRAIN_STATE, completed_summary)}\n\n"
        f"{untrusted_content_directive((TAG_BRAIN_STATE,))}\n\n"
        f"Return your revised plan as a JSON object with the same schema:\n\n"
        f"{_REPLAN_JSON_EXAMPLE}\n\n"
        "Return ONLY the JSON object, no other text."
    )
    replan_msg = ChatMessage(role=MessageRole.USER, content=replan_content)
    result = await call_planner(
        run,
        state.ctx,
        state.turns,
        replan_msg,
        revision_number=plan.revision_number + 1,
    )
    if isinstance(result, ExecutionResult):
        return finalize(result, state.all_plans, state.replans_used)
    state.ctx, new_plan = result
    state.ctx = state.ctx.cleared_pending_replan()
    state.plan = new_plan
    state.all_plans.append(new_plan)
    logger.info(
        EXECUTION_PLAN_REPLAN_COMPLETE,
        execution_id=state.ctx.execution_id,
        step_count=len(new_plan.steps),
        revision=new_plan.revision_number,
    )
    return None
