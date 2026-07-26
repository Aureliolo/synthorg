# module-kind: code
"""The mini-ReAct turn pipeline both plan-based loops run.

``HybridLoop`` and ``PlanExecuteLoop`` execute a plan step the same way: a
sub-loop of provider turns that ends when the model stops calling tools.
Only the surrounding step walk differs, so the turn itself lives here once
and both loops inherit it. Keeping two copies in step meant every fix to
the turn contract had to land twice.
"""

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.intervention.loop_hook import check_steering
from synthorg.engine.loop_cancellation import check_task_cancelled
from synthorg.engine.loop_control_helpers import check_budget, check_shutdown
from synthorg.engine.loop_helpers import (
    check_response_errors,
    classify_turn,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.loop_streaming import (
    _TurnInterrupted,
    fold_interrupt_usage,
    run_provider_turn,
)
from synthorg.engine.loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from synthorg.engine.plan_helpers import (
    assess_step_success,
    invoke_checkpoint_callback,
)
from synthorg.engine.plan_loop_context import (
    StepRunContext,
    StepRunState,
    StepTurnOutcome,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_STEP_TOOL_USE_EMPTY,
    EXECUTION_PLAN_STEP_TRUNCATED,
)
from synthorg.providers.models import CompletionResponse, ToolDefinition

logger = get_logger(__name__)


class PlanStepTurnMixin:
    """One provider turn inside a plan step, shared by both plan loops."""

    # Set in each concrete loop's ``__init__``.
    _approval_gate: ApprovalGate | None
    _checkpoint_callback: CheckpointCallback | None
    _steering_inbox: SteeringInbox | None

    async def _run_step_turn(
        self,
        run: StepRunContext,
        state: StepRunState,
        tool_defs: list[ToolDefinition] | None,
    ) -> StepTurnOutcome | ExecutionResult:
        """Execute a single turn within a step's mini-ReAct sub-loop.

        Returns:
            :attr:`StepTurnOutcome.CONTINUE` to keep the sub-loop running
            (also the re-issue path after a mid-turn steering REDIRECT),
            ``STEP_SUCCEEDED`` / ``STEP_FAILED`` once the step concludes, or
            an :class:`ExecutionResult` to terminate the run: shutdown,
            budget, cancellation, a provider error, or an approval-gate or
            tool-failure outcome from the tool-call arm.
        """
        blocked = await self._pre_turn_checks(run, state)
        if blocked is not None:
            return blocked

        turn_number = state.ctx.turn_count + 1
        outcome = await run_provider_turn(
            state.ctx,
            run.provider,
            run.executor_model,
            tool_defs=tool_defs,
            config=run.completion_config,
            turn_number=turn_number,
            turns=state.turns,
            streaming_enabled=run.streaming_enabled,
            cancellation_checker=run.task_cancellation_checker,
            steering_inbox=self._steering_inbox,
        )
        if isinstance(outcome, ExecutionResult):
            return outcome
        if isinstance(outcome, _TurnInterrupted):
            # Re-issue the step turn: continuing the mini-sub-loop lets its
            # top-of-turn steering check adopt the REDIRECT the interrupt
            # fired for.
            state.ctx = fold_interrupt_usage(state.ctx, outcome)
            return StepTurnOutcome.CONTINUE

        return await self._finish_turn(run, state, outcome, turn_number)

    async def _pre_turn_checks(
        self,
        run: StepRunContext,
        state: StepRunState,
    ) -> ExecutionResult | None:
        """Run the pre-LLM guards and adopt any pending steering directive.

        Returns:
            A terminal :class:`ExecutionResult` when shutdown, budget, or
            cancellation trips, or ``None`` when the turn may proceed.
        """
        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            return shutdown_result
        budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
        if budget_result is not None:
            return budget_result
        cancel_result = await check_task_cancelled(
            state.ctx, run.task_cancellation_checker, state.turns
        )
        if cancel_result is not None:
            return cancel_result
        # Adopt pending steering directives before the LLM call so the
        # operator's constraint is in context for this step's turn.
        steered = await check_steering(state.ctx, self._steering_inbox)
        if steered is not None:
            state.ctx = steered
        return None

    async def _finish_turn(
        self,
        run: StepRunContext,
        state: StepRunState,
        response: CompletionResponse,
        turn_number: int,
    ) -> StepTurnOutcome | ExecutionResult:
        """Record a completed provider turn and dispatch on its tool calls.

        Returns:
            :attr:`StepTurnOutcome.CONTINUE` when tool calls ran and the
            sub-loop should issue another turn, ``STEP_SUCCEEDED`` /
            ``STEP_FAILED`` when this turn concluded the step, or an
            :class:`ExecutionResult` for a provider error or a terminal
            tool-execution outcome.
        """
        state.turns.append(
            make_turn_record(
                turn_number,
                response,
                call_category=classify_turn(turn_number, response, state.ctx),
                provider_metadata=response.provider_metadata,
            )
        )

        error = check_response_errors(
            state.ctx,
            response,
            turn_number,
            state.turns,
        )
        if error is not None:
            return error

        state.ctx = state.ctx.with_turn_completed(
            response.usage,
            response_to_message(response),
        )
        logger.info(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=state.ctx.execution_id,
            turn=turn_number,
            finish_reason=response.finish_reason.value,
            tool_call_count=len(response.tool_calls),
        )

        await invoke_checkpoint_callback(
            run.checkpoint_callback,
            state.ctx,
            turn_number,
        )

        if not response.tool_calls:
            state.ctx, step_ok = self._handle_step_completion(
                state.ctx, response, turn_number
            )
            return StepTurnOutcome.from_success(success=step_ok)

        return await self._handle_step_tool_calls(
            run,
            state,
            response,
            turn_number,
        )

    def _handle_step_completion(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turn_number: int,
    ) -> tuple[AgentContext, bool]:
        """Assess step success and log the two provider anomalies.

        Returns:
            ``(ctx, success)``: the unchanged context and the step's
            success flag from :func:`assess_step_success`.
        """
        if response.finish_reason == FinishReason.TOOL_USE:
            # Its own event, not the routine per-turn one: a consumer
            # filtering for turn completions would otherwise get this error
            # under the same name with an entirely different field set.
            logger.error(
                EXECUTION_PLAN_STEP_TOOL_USE_EMPTY,
                execution_id=ctx.execution_id,
                turn=turn_number,
                finish_reason=response.finish_reason.value,
                error="Provider returned TOOL_USE with no tool calls",
            )
            return ctx, False
        success = assess_step_success(response)
        if response.finish_reason == FinishReason.MAX_TOKENS:
            logger.warning(
                EXECUTION_PLAN_STEP_TRUNCATED,
                execution_id=ctx.execution_id,
                turn=turn_number,
                truncated=True,
            )
        return ctx, success

    async def _handle_step_tool_calls(
        self,
        run: StepRunContext,
        state: StepRunState,
        response: CompletionResponse,
        turn_number: int,
    ) -> StepTurnOutcome | ExecutionResult:
        """Check shutdown and execute tool calls for a step turn.

        Returns:
            :attr:`StepTurnOutcome.CONTINUE` once the tool outputs are
            appended to ``state.ctx``, or an :class:`ExecutionResult` when
            shutdown fires or tool execution terminates the loop (a missing
            invoker, a tool failure, or an approval-gate escalation).
        """
        shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(state.turns)
            return shutdown_result.model_copy(
                update={"turns": tuple(state.turns)},
            )

        result = await execute_tool_calls(
            state.ctx,
            run.tool_invoker,
            response,
            turn_number,
            state.turns,
            approval_gate=self._approval_gate,
        )
        if isinstance(result, ExecutionResult):
            return result
        state.ctx = result
        return StepTurnOutcome.CONTINUE
