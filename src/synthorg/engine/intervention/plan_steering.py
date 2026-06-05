"""Steering-driven replan for the Plan/Hybrid loops.

When a REDIRECT directive is adopted mid-step, the loop records a pending replan
on the context; at the next step boundary it forces a plan revision so the
remaining work honours the directive. This lives outside the loop modules (which
are at their size budgets) as a free function that receives the loop's planner
and finalize callables, keeping it decoupled from the loop class internals.
"""

from typing import TYPE_CHECKING, Protocol

from synthorg.engine.plan_models import ExecutionPlan, StepStatus
from synthorg.engine.plan_parsing import _REPLAN_JSON_EXAMPLE
from synthorg.engine.prompt_safety import (
    TAG_BRAIN_STATE,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_START,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.execution.turn import TurnRecord
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


class _PlannerCall(Protocol):
    """The loop's ``_call_planner`` bound method."""

    async def __call__(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
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


async def steering_replan(  # noqa: PLR0913
    *,
    ctx: AgentContext,
    provider: CompletionProvider,
    planner_model: str,
    config: CompletionConfig,
    plan: ExecutionPlan,
    turns: list[TurnRecord],
    all_plans: list[ExecutionPlan],
    replans_used: int,
    call_planner: _PlannerCall,
    finalize: _Finalize,
) -> tuple[AgentContext, ExecutionPlan, int] | ExecutionResult:
    """Replan after adopting a mid-flight steering REDIRECT.

    The directive is already in ``ctx`` (injected at the turn boundary); this
    revises the plan for the remaining work to honour it. Unlike a failure
    replan it does not count against ``max_replans`` because it is
    operator-driven and consume-once (the directive id is cleared here).

    Returns:
        ``(ctx, new_plan, replans_used)`` on a successful replan with the
        pending-replan flag cleared, or a terminal :class:`ExecutionResult`.
    """
    from synthorg.engine.loop_protocol import ExecutionResult  # noqa: PLC0415

    logger.info(
        EXECUTION_PLAN_REPLAN_START,
        execution_id=ctx.execution_id,
        trigger="steering",
        directive_id=ctx.pending_steering_replan_id,
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
        ctx,
        provider,
        planner_model,
        config,
        turns,
        replan_msg,
        revision_number=plan.revision_number + 1,
    )
    if isinstance(result, ExecutionResult):
        return finalize(result, all_plans, replans_used)
    ctx, new_plan = result
    ctx = ctx.cleared_pending_replan()
    all_plans.append(new_plan)
    logger.info(
        EXECUTION_PLAN_REPLAN_COMPLETE,
        execution_id=ctx.execution_id,
        step_count=len(new_plan.steps),
        revision=new_plan.revision_number,
    )
    return ctx, new_plan, replans_used
