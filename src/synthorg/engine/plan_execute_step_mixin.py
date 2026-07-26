"""Result finalization for :class:`PlanExecuteLoop`.

Carries the plan-execute metadata shape and the terminal classification
once the step walk stops. The per-turn machinery is shared with
``HybridLoop`` and lives in :mod:`synthorg.engine.plan_step_turn`.
"""

import copy

from synthorg.engine.loop_helpers import build_result
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.plan_loop_context import StepRunState
from synthorg.engine.plan_models import ExecutionPlan
from synthorg.engine.plan_step_turn import PlanStepTurnMixin
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_TERMINATED

logger = get_logger(__name__)


class PlanExecuteStepMixin(PlanStepTurnMixin):
    """Mixin providing the step-execution helpers for PlanExecuteLoop."""

    @staticmethod
    def _finalize(
        result: ExecutionResult,
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Attach plan metadata to the execution result.

        Returns:
            A copy of ``result`` with ``loop_type='plan_execute'``,
            the final plan, and the replan counter merged into
            ``metadata``.
        """
        metadata = copy.deepcopy(result.metadata)
        metadata.update(
            {
                "loop_type": "plan_execute",
                "plans": [p.model_dump() for p in all_plans],
                "final_plan": (all_plans[-1].model_dump() if all_plans else None),
                "replans_used": replans_used,
            }
        )
        return result.model_copy(update={"metadata": metadata})

    def _build_final_result(self, state: StepRunState) -> ExecutionResult:
        """Build the final result after step iteration completes.

        Returns:
            The terminal :class:`ExecutionResult` with ``MAX_TURNS``
            when turns ran out mid-plan and ``COMPLETED`` otherwise.
        """
        # Sync live plan so final_plan metadata reflects step statuses
        state.sync_current_plan()
        if not state.ctx.has_turns_remaining and state.step_idx < len(state.plan.steps):
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=state.ctx.execution_id,
                reason=TerminationReason.MAX_TURNS.value,
                turns=len(state.turns),
            )
            return self._finalize(
                build_result(state.ctx, TerminationReason.MAX_TURNS, state.turns),
                state.all_plans,
                state.replans_used,
            )

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=state.ctx.execution_id,
            reason=TerminationReason.COMPLETED.value,
            turns=len(state.turns),
        )
        return self._finalize(
            build_result(state.ctx, TerminationReason.COMPLETED, state.turns),
            state.all_plans,
            state.replans_used,
        )
