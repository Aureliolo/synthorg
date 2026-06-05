"""Planning and re-planning phase for the Plan-and-Execute loop.

Provides ``PlanExecutePlannerMixin``: the initial plan generation, the
shared planner LLM call, the failure-driven re-plan, and the replan-
budget orchestration. Built on :class:`PlanExecuteStepMixin` so it can
reuse the per-turn checkpoint seam.
"""

from typing import TYPE_CHECKING

from synthorg.engine.plan_execute_step_mixin import PlanExecuteStepMixin
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_CREATED,
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_EXHAUSTED,
    EXECUTION_PLAN_REPLAN_START,
    EXECUTION_PLAN_STEP_FAILED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

from .loop_control_helpers import check_budget, check_shutdown
from .loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    classify_turn,
    make_turn_record,
    response_to_message,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
    TurnRecord,
)
from .plan_helpers import extract_task_summary, update_step_status
from .plan_models import ExecutionPlan, PlanExecuteConfig, PlanStep, StepStatus
from .plan_parsing import _PLANNING_PROMPT, _REPLAN_JSON_EXAMPLE, parse_plan

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


class PlanExecutePlannerMixin(PlanExecuteStepMixin):
    """Planning, re-planning, and replan-budget orchestration."""

    # Populated on the concrete ``PlanExecuteLoop`` in ``__init__``.
    _config: PlanExecuteConfig

    async def _run_planning_phase(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
        shutdown_checker: ShutdownChecker | None,
        budget_checker: BudgetChecker | None,
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Run pre-checks and generate the initial plan.

        Returns:
            ``(updated_ctx, plan)`` when shutdown / budget checks pass
            and the planner succeeds; a terminal :class:`ExecutionResult`
            when any pre-check trips so the caller bails out early.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return budget_result
        return await self._generate_plan(
            ctx,
            provider,
            planner_model,
            config,
            turns,
        )

    async def _attempt_replan(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        plan: ExecutionPlan,
        step: PlanStep,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
    ) -> tuple[AgentContext, ExecutionPlan, int] | ExecutionResult:
        """Handle a failed step: mark it, check replan budget, replan.

        Returns:
            ``(ctx, new_plan, replans_used)`` on successful replan, or
            ``ExecutionResult`` for termination conditions.
        """
        plan = update_step_status(plan, step_idx, StepStatus.FAILED)
        logger.warning(
            EXECUTION_PLAN_STEP_FAILED,
            execution_id=ctx.execution_id,
            step_number=step.step_number,
        )

        if replans_used >= self._config.max_replans:
            logger.error(
                EXECUTION_PLAN_REPLAN_EXHAUSTED,
                execution_id=ctx.execution_id,
                replans_used=replans_used,
                max_replans=self._config.max_replans,
            )
            error_msg = (
                f"Max replans ({self._config.max_replans}) exhausted "
                f"after step {step.step_number} failed"
            )
            return self._finalize(
                build_result(
                    ctx,
                    TerminationReason.ERROR,
                    turns,
                    error_message=error_msg,
                ),
                all_plans,
                replans_used,
            )

        if not ctx.has_turns_remaining:
            return self._finalize(
                build_result(ctx, TerminationReason.MAX_TURNS, turns),
                all_plans,
                replans_used,
            )

        # Check shutdown/budget before replanning LLM call
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return self._finalize(shutdown_result, all_plans, replans_used)
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return self._finalize(budget_result, all_plans, replans_used)

        replan_result = await self._replan(
            ctx,
            provider,
            planner_model,
            config,
            plan,
            step,
            turns,
        )
        if isinstance(replan_result, ExecutionResult):
            return self._finalize(replan_result, all_plans, replans_used)

        ctx, new_plan = replan_result
        replans_used += 1
        all_plans.append(new_plan)
        return ctx, new_plan, replans_used

    async def _generate_plan(
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate an execution plan from the LLM.

        Returns:
            ``(updated_ctx, plan)`` on a successful plan generation,
            or the terminal :class:`ExecutionResult` propagated from
            the planner call (budget exhaustion, shutdown, etc.).
        """
        plan_msg = ChatMessage(
            role=MessageRole.USER,
            content=_PLANNING_PROMPT,
        )
        result = await self._call_planner(
            ctx,
            provider,
            planner_model,
            config,
            turns,
            plan_msg,
        )
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        logger.info(
            EXECUTION_PLAN_CREATED,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    async def _replan(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        planner_model: str,
        config: CompletionConfig,
        current_plan: ExecutionPlan,
        failed_step: PlanStep,
        turns: list[TurnRecord],
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Generate a revised plan after a step failure.

        Returns:
            ``(updated_ctx, new_plan)`` carrying the revised plan with
            ``revision_number`` incremented; the terminal
            :class:`ExecutionResult` when the planner call fails.
        """
        logger.info(
            EXECUTION_PLAN_REPLAN_START,
            execution_id=ctx.execution_id,
            failed_step=failed_step.step_number,
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
        result = await self._call_planner(
            ctx,
            provider,
            planner_model,
            config,
            turns,
            replan_msg,
            revision_number=current_plan.revision_number + 1,
        )
        if isinstance(result, ExecutionResult):
            return result
        ctx, plan = result
        logger.info(
            EXECUTION_PLAN_REPLAN_COMPLETE,
            execution_id=ctx.execution_id,
            step_count=len(plan.steps),
            revision=plan.revision_number,
        )
        return ctx, plan

    async def _call_planner(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
        turns: list[TurnRecord],
        message: ChatMessage,
        *,
        revision_number: int = 0,
    ) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
        """Shared body for plan generation and re-planning.

        Sends the message to the LLM, records the turn, checks for
        response errors, parses the plan, and returns either
        ``(ctx, plan)`` or an error result.

        Returns:
            ``(updated_ctx, parsed_plan)`` on a successful planner
            call and parse; a terminal :class:`ExecutionResult` when
            the planner errored or parsing failed.
        """
        task_summary = extract_task_summary(ctx)
        ctx = ctx.with_message(message)
        turn_number = ctx.turn_count + 1

        response = await call_provider(
            ctx,
            provider,
            model,
            None,
            config,
            turn_number,
            turns,
        )
        if isinstance(response, ExecutionResult):
            return response

        turns.append(
            make_turn_record(
                turn_number,
                response,
                call_category=classify_turn(
                    turn_number,
                    response,
                    ctx,
                    is_planning_phase=True,
                ),
                provider_metadata=response.provider_metadata,
            )
        )

        error = check_response_errors(ctx, response, turn_number, turns)
        if error is not None:
            return error

        ctx = ctx.with_turn_completed(
            response.usage,
            response_to_message(response),
        )
        logger.info(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=ctx.execution_id,
            turn=turn_number,
            finish_reason=response.finish_reason.value,
            tool_call_count=0,
        )

        await self._invoke_checkpoint_callback(ctx, turn_number)

        plan = parse_plan(
            response,
            ctx.execution_id,
            task_summary,
            revision_number=revision_number,
        )
        if plan is None:
            error_msg = "Failed to parse execution plan from LLM response"
            return build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )
        return ctx, plan
