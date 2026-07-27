"""Re-planning phase for the Plan-and-Execute loop.

Provides ``PlanExecutePlannerMixin``: the failure-driven re-plan and the
replan-budget orchestration. The opening planning phase and the terminal
result shape are shared with ``HybridLoop`` and live in
:class:`~synthorg.engine.plan_phases.PlanPhaseMixin`.
"""

from synthorg.engine.plan_phases import PlanPhaseMixin
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_EXHAUSTED,
    EXECUTION_PLAN_REPLAN_START,
    EXECUTION_PLAN_STEP_FAILED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

from .loop_control_helpers import check_budget, check_shutdown
from .loop_helpers import (
    build_result,
)
from .loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from .plan_helpers import call_planner, update_step_status
from .plan_loop_context import ReplanTrigger, StepRunContext, StepRunState
from .plan_models import ExecutionPlan, PlanExecuteConfig, PlanStep, StepStatus
from .plan_parsing import _REPLAN_JSON_EXAMPLE

logger = get_logger(__name__)


class PlanExecutePlannerMixin(PlanPhaseMixin):
    """Re-planning and replan-budget orchestration."""

    _LOOP_TYPE = "plan_execute"

    # Populated on the concrete ``PlanExecuteLoop`` in ``__init__``.
    _config: PlanExecuteConfig

    async def _attempt_replan(
        self,
        run: StepRunContext,
        state: StepRunState,
        step: PlanStep,
    ) -> ExecutionResult | None:
        """Handle a failed step: mark it, check replan budget, replan.

        On success the revised plan is adopted onto ``state`` (plan
        rebound, replan counter incremented, plan appended to the history).

        Returns:
            ``None`` once the revised plan is adopted, or an
            :class:`ExecutionResult` for termination conditions.
        """
        state.plan = update_step_status(state.plan, state.step_idx, StepStatus.FAILED)
        # Every early return below finalises straight from ``all_plans``,
        # bypassing the tail's resync, so the FAILED status has to land in
        # the history now or the terminal ``final_plan`` loses it.
        state.sync_current_plan()
        logger.warning(
            EXECUTION_PLAN_STEP_FAILED,
            execution_id=state.ctx.execution_id,
            step_number=step.step_number,
        )

        if state.replans_used >= self._config.max_replans:
            logger.error(
                EXECUTION_PLAN_REPLAN_EXHAUSTED,
                execution_id=state.ctx.execution_id,
                replans_used=state.replans_used,
                max_replans=self._config.max_replans,
            )
            error_msg = (
                f"Max replans ({self._config.max_replans}) exhausted "
                f"after step {step.step_number} failed"
            )
            return self._finalize(
                build_result(
                    state.ctx,
                    TerminationReason.ERROR,
                    state.turns,
                    error_message=error_msg,
                ),
                state.all_plans,
                state.replans_used,
            )

        if not state.ctx.has_turns_remaining:
            return self._finalize(
                build_result(state.ctx, TerminationReason.MAX_TURNS, state.turns),
                state.all_plans,
                state.replans_used,
            )

        # Check shutdown/budget before replanning LLM call
        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            return self._finalize(shutdown_result, state.all_plans, state.replans_used)
        budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
        if budget_result is not None:
            return self._finalize(budget_result, state.all_plans, state.replans_used)

        replan_result = await self._replan(run, state, step)
        if isinstance(replan_result, ExecutionResult):
            return self._finalize(replan_result, state.all_plans, state.replans_used)

        state.record_replan(replan_result)
        return None

    async def _replan(
        self,
        run: StepRunContext,
        state: StepRunState,
        failed_step: PlanStep,
    ) -> ExecutionPlan | ExecutionResult:
        """Generate a revised plan after a step failure.

        Advances ``state.ctx`` across the planner turn; adopting the
        returned plan is left to the caller, which also owns the replan
        budget.

        Returns:
            The revised :class:`ExecutionPlan` with ``revision_number``
            incremented; the terminal :class:`ExecutionResult` when the
            planner call fails.
        """
        current_plan = state.plan
        logger.info(
            EXECUTION_PLAN_REPLAN_START,
            execution_id=state.ctx.execution_id,
            trigger=ReplanTrigger.STEP_FAILURE.value,
            step_number=failed_step.step_number,
            directive_id=state.ctx.pending_steering_replan_id,
            revision=current_plan.revision_number,
        )

        completed_summary = (
            "\n".join(
                f"  Step {s.step_number}: {s.description} -> COMPLETED"
                for s in current_plan.steps
                if s.status == StepStatus.COMPLETED
            )
            or "  (none)"
        )

        replan_content = (
            f"Step {failed_step.step_number} failed: "
            f"{failed_step.description}\n\n"
            f"Completed steps so far:\n{completed_summary}\n\n"
            f"Create a revised plan for the REMAINING work. "
            f"Return your revised plan as a JSON object with the "
            f"same schema:\n\n{_REPLAN_JSON_EXAMPLE}\n\n"
            f"Return ONLY the JSON object, no other text."
        )
        replan_msg = ChatMessage(
            role=MessageRole.USER,
            content=replan_content,
        )
        result = await call_planner(
            run,
            state.ctx,
            state.turns,
            replan_msg,
            revision_number=current_plan.revision_number + 1,
        )
        if isinstance(result, ExecutionResult):
            return result
        state.ctx, plan = result
        logger.info(
            EXECUTION_PLAN_REPLAN_COMPLETE,
            execution_id=state.ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return plan
