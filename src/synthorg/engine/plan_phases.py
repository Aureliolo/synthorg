# module-kind: code
"""Run-phase orchestration shared by the two plan-based loops.

Above the per-turn pipeline in :mod:`synthorg.engine.plan_step_turn` sits a
second layer ``HybridLoop`` and ``PlanExecuteLoop`` run identically: the
guarded opening planning phase, the planner call that produces the first
plan, the terminal classification once the step walk stops, and the plan
metadata each run carries out. Only two things genuinely differ, and both
are declared per loop instead of re-implemented: the ``loop_type`` tag on
the result metadata, and whether a generated plan is capped to a configured
step ceiling.
"""

import copy

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_control_helpers import check_budget, check_shutdown
from synthorg.engine.loop_helpers import build_result
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.plan_helpers import call_planner
from synthorg.engine.plan_loop_context import StepRunContext, StepRunState
from synthorg.engine.plan_models import ExecutionPlan
from synthorg.engine.plan_parsing import _PLANNING_PROMPT
from synthorg.engine.plan_step_turn import PlanStepTurnMixin
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_PLAN_CREATED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)


class PlanPhaseMixin(PlanStepTurnMixin):
    """Planning, terminal classification, and result metadata for both loops."""

    # Bound by each concrete loop. Consumers filter runs on this tag, so it
    # is a per-loop value rather than something derived from the class name.
    _LOOP_TYPE: str

    def get_loop_type(self) -> str:
        """Return the loop type identifier.

        Returns:
            The loop's ``_LOOP_TYPE``, the same tag ``_finalize`` writes into
            the result metadata, so the protocol accessor and the metadata
            can never drift apart.
        """
        return self._LOOP_TYPE

    async def _run_planning_phase(
        self,
        run: StepRunContext,
        ctx: AgentContext,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Run pre-checks and generate the initial plan.

        Returns:
            ``(updated_ctx, plan)`` when shutdown / budget checks pass
            and the planner succeeds; the terminal :class:`ExecutionResult`
            when any pre-check trips so the caller bails out early.
        """
        shutdown_result = check_shutdown(ctx, run.shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, run.budget_checker, turns)
        if budget_result is not None:
            return budget_result
        return await self._generate_plan(run, ctx, turns)

    async def _generate_plan(
        self,
        run: StepRunContext,
        ctx: AgentContext,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate an execution plan from the LLM.

        Returns:
            ``(updated_ctx, plan)`` on a successful plan generation,
            or the terminal :class:`ExecutionResult` propagated from
            :func:`call_planner` (budget exhaustion, shutdown, etc.).
        """
        plan_msg = ChatMessage(
            role=MessageRole.USER,
            content=_PLANNING_PROMPT,
        )
        result = await call_planner(run, ctx, turns, plan_msg)
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        plan = self._apply_plan_limits(plan)
        logger.info(
            EXECUTION_PLAN_CREATED,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    def _apply_plan_limits(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Cap a freshly generated plan to the loop's step ceiling.

        Returns:
            The plan unchanged. A loop whose configuration carries a step
            ceiling overrides this; one without a configured ceiling honours
            whatever the planner produced rather than inventing a limit.
        """
        return plan

    def _build_final_result(self, state: StepRunState) -> ExecutionResult:
        """Build the final result after step iteration completes.

        Returns:
            The terminal :class:`ExecutionResult`, with a ``MAX_TURNS``
            termination reason when turns ran out mid-plan and
            ``COMPLETED`` otherwise.
        """
        # Sync the live plan into all_plans so final_plan reflects step
        # status changes (COMPLETED, IN_PROGRESS, etc.).
        state.sync_current_plan()

        ran_out = not state.ctx.has_turns_remaining and state.step_idx < len(
            state.plan.steps
        )
        reason = TerminationReason.MAX_TURNS if ran_out else TerminationReason.COMPLETED
        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=state.ctx.execution_id,
            reason=reason.value,
            turns=len(state.turns),
        )
        return self._finalize(
            build_result(state.ctx, reason, state.turns),
            state.all_plans,
            state.replans_used,
        )

    @classmethod
    def _finalize(
        cls,
        result: ExecutionResult,
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Attach the loop's plan metadata to the execution result.

        Returns:
            A copy of ``result`` whose metadata carries the loop type,
            the plan history, the final plan dump, and the replan count.
        """
        metadata = copy.deepcopy(result.metadata)
        metadata.update(
            {
                "loop_type": cls._LOOP_TYPE,
                "plans": [p.model_dump() for p in all_plans],
                "final_plan": (all_plans[-1].model_dump() if all_plans else None),
                "replans_used": replans_used,
            }
        )
        return result.model_copy(update={"metadata": metadata})
