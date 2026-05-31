"""Step-execution mixin for :class:`PlanExecuteLoop`.

Extracts step-turn running, completion handling, tool-call dispatch,
checkpoint invocation, and result finalization into a mixin so the
top-level loop file stays under the project's size limit.
"""

import copy
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.intervention.loop_hook import check_steering
from synthorg.engine.loop_cancellation import check_task_cancelled
from synthorg.engine.loop_helpers import (
    build_result,
    call_provider,
    check_budget,
    check_response_errors,
    check_shutdown,
    classify_turn,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from synthorg.engine.plan_helpers import assess_step_success
from synthorg.engine.plan_models import ExecutionPlan
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_STEP_TRUNCATED,
)
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.context import AgentContext
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.providers.models import (
        CompletionConfig,
        CompletionResponse,
        ToolDefinition,
    )
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)


class PlanExecuteStepMixin:
    """Mixin providing the step-execution helpers for PlanExecuteLoop."""

    _approval_gate: ApprovalGate | None
    _checkpoint_callback: CheckpointCallback | None
    _steering_inbox: SteeringInbox | None

    async def _run_step_turn(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model: str,
        config: CompletionConfig,
        tool_defs: list[ToolDefinition] | None,
        tool_invoker: ToolInvokerProtocol | None,
        turns: list[TurnRecord],
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> AgentContext | ExecutionResult | tuple[AgentContext, bool]:
        """Execute a single turn within a step's mini-ReAct sub-loop.

        Returns:
            The updated :class:`AgentContext` to continue the
            sub-loop, an :class:`ExecutionResult` to terminate
            execution (shutdown / budget / cancellation), or
            ``(ctx, True)`` when the step completed successfully.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(ctx, budget_checker, turns)
        if budget_result is not None:
            return budget_result
        cancel_result = await check_task_cancelled(
            ctx, task_cancellation_checker, turns
        )
        if cancel_result is not None:
            return cancel_result
        # Adopt pending steering directives before the LLM call so the
        # operator's constraint is in context for this step's turn.
        steered = await check_steering(
            ctx, self._steering_inbox, execution_id=ctx.execution_id
        )
        if steered is not None:
            ctx = steered

        turn_number = ctx.turn_count + 1
        response = await call_provider(
            ctx,
            provider,
            model,
            tool_defs,
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
                call_category=classify_turn(turn_number, response, ctx),
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
            tool_call_count=len(response.tool_calls),
        )

        await self._invoke_checkpoint_callback(ctx, turn_number)

        if not response.tool_calls:
            return self._handle_step_completion(ctx, response, turn_number)

        return await self._handle_step_tool_calls(
            ctx,
            tool_invoker,
            response,
            turn_number,
            turns,
            shutdown_checker,
        )

    def _handle_step_completion(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turn_number: int,
    ) -> tuple[AgentContext, bool]:
        """Assess step success and log truncation if applicable.

        Returns:
            ``(ctx, success)``: the unchanged context and the step's
            success flag from :func:`assess_step_success`.
        """
        success = assess_step_success(response)
        if response.finish_reason == FinishReason.MAX_TOKENS:
            logger.warning(
                EXECUTION_PLAN_STEP_TRUNCATED,
                execution_id=ctx.execution_id,
                turn=turn_number,
                truncated=True,
            )
        return ctx, success

    async def _handle_step_tool_calls(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        tool_invoker: ToolInvokerProtocol | None,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
        shutdown_checker: ShutdownChecker | None,
    ) -> AgentContext | ExecutionResult:
        """Check shutdown and execute tool calls for a step turn.

        Returns:
            The updated :class:`AgentContext` after tool execution
            appends results, or an :class:`ExecutionResult` when
            shutdown fires or tool execution terminates the loop.
        """
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(turns)
            return shutdown_result.model_copy(
                update={"turns": tuple(turns)},
            )

        return await execute_tool_calls(
            ctx,
            tool_invoker,
            response,
            turn_number,
            turns,
            approval_gate=self._approval_gate,
        )

    async def _invoke_checkpoint_callback(
        self,
        ctx: AgentContext,
        turn_number: int,
    ) -> None:
        """Invoke the checkpoint callback if configured.

        Non-critical errors are logged and suppressed so checkpointing
        does not interrupt execution; critical system errors are
        re-raised via :func:`reraise_critical`.

        Raises:
            MemoryError: Propagated as a critical system error.
            RecursionError: Propagated as a critical system error.
        """
        if self._checkpoint_callback is None:
            return
        try:
            await self._checkpoint_callback(ctx)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXECUTION_CHECKPOINT_CALLBACK_FAILED,
                execution_id=ctx.execution_id,
                turn=turn_number,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

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

    def _build_final_result(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        plan: ExecutionPlan,
        step_idx: int,
        turns: list[TurnRecord],
        all_plans: list[ExecutionPlan],
        replans_used: int,
    ) -> ExecutionResult:
        """Build the final result after step iteration completes.

        Returns:
            The terminal :class:`ExecutionResult` with ``MAX_TURNS``
            when turns ran out mid-plan and ``COMPLETED`` otherwise.
        """
        # Sync live plan so final_plan metadata reflects step statuses
        if all_plans:
            all_plans[-1] = plan
        if not ctx.has_turns_remaining and step_idx < len(plan.steps):
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.MAX_TURNS.value,
                turns=len(turns),
            )
            return self._finalize(
                build_result(ctx, TerminationReason.MAX_TURNS, turns),
                all_plans,
                replans_used,
            )

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.COMPLETED.value,
            turns=len(turns),
        )
        return self._finalize(
            build_result(ctx, TerminationReason.COMPLETED, turns),
            all_plans,
            replans_used,
        )
