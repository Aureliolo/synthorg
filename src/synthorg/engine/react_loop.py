"""ReAct execution loop -- think, act, observe.

Implements the ``ExecutionLoop`` protocol using the ReAct pattern:
check shutdown -> check budget -> call LLM -> record turn ->
check for LLM errors -> update context -> handle completion or
(check shutdown -> execute tools) -> repeat.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TURN_COMPLETE,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionConfig,
    CompletionResponse,
)

from .intervention.loop_hook import check_steering
from .loop_cancellation import check_task_cancelled
from .loop_control_helpers import (
    check_budget,
    check_shutdown,
    check_stagnation,
    invoke_compaction,
)
from .loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    classify_turn,
    get_tool_definitions,
    make_turn_record,
    response_to_message,
)
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnRecord,
)
from .loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.context import AgentContext
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.providers.models import ToolDefinition
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)


class ReactLoop:
    """ReAct execution loop: reason, act, observe.

    The loop checks for shutdown, checks the budget, calls the LLM,
    checks for termination conditions, executes any requested tools,
    feeds results back, and repeats until the LLM signals completion,
    the turn limit is reached, the budget is exhausted, a shutdown is
    requested, or an error occurs.

    Args:
        checkpoint_callback: Optional async callback invoked after each
            completed turn; the callback itself decides whether to persist.
        approval_gate: Optional gate that checks for pending escalations
            after tool execution and parks the agent when approval is
            required.  ``None`` disables approval checks.
        stagnation_detector: Optional detector that checks for
            repetitive tool-call patterns and intervenes with
            corrective prompts or early termination.  ``None``
            disables stagnation detection.
        compaction_callback: Optional async callback invoked at turn
            boundaries to compress older conversation turns when the
            context fill level is high.  ``None`` disables compaction.
    """

    def __init__(
        self,
        checkpoint_callback: CheckpointCallback | None = None,
        *,
        approval_gate: ApprovalGate | None = None,
        stagnation_detector: StagnationDetector | None = None,
        compaction_callback: CompactionCallback | None = None,
        steering_inbox: SteeringInbox | None = None,
    ) -> None:
        self._checkpoint_callback = checkpoint_callback
        self._approval_gate = approval_gate
        self._stagnation_detector = stagnation_detector
        self._compaction_callback = compaction_callback
        self._steering_inbox = steering_inbox

    @property
    def approval_gate(self) -> ApprovalGate | None:
        """Return the approval gate, or ``None``."""
        return self._approval_gate

    @property
    def stagnation_detector(self) -> StagnationDetector | None:
        """Return the stagnation detector, or ``None``."""
        return self._stagnation_detector

    @property
    def compaction_callback(self) -> CompactionCallback | None:
        """Return the compaction callback, or ``None``."""
        return self._compaction_callback

    @property
    def steering_inbox(self) -> SteeringInbox | None:
        """Return the steering inbox, or ``None``."""
        return self._steering_inbox

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "react"

    async def execute(  # noqa: PLR0913
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvokerProtocol | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
    ) -> ExecutionResult:
        """Run the ReAct loop until termination.

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional budget exhaustion callback.
            shutdown_checker: Optional callback; returns ``True`` when
                a graceful shutdown has been requested.
            completion_config: Optional per-execution config override.
            task_cancellation_checker: Optional async callback; returns
                ``True`` when the task was cancelled/superseded externally.

        Returns:
            Execution result with final context and termination info.

        Raises:
            MemoryError: Re-raised unconditionally (non-recoverable).
            RecursionError: Re-raised unconditionally (non-recoverable).
        """
        model_id, config, tool_defs, turns = self._prepare_loop(
            context, completion_config, tool_invoker
        )
        ctx = context
        corrections_injected = 0

        while ctx.has_turns_remaining:
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

            # Adopt any pending steering directives before the LLM call so
            # the operator's constraint is in context for this turn.
            steered = await check_steering(
                ctx, self._steering_inbox, execution_id=ctx.execution_id
            )
            if steered is not None:
                ctx = steered

            # Refresh tool defs each turn so newly loaded tools appear
            tool_defs = get_tool_definitions(tool_invoker, ctx.loaded_tools)

            turn_number = ctx.turn_count + 1
            response = await call_provider(
                ctx,
                provider,
                model_id,
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

            result = await self._process_turn_response(
                ctx,
                response,
                turn_number,
                turns,
                tool_invoker,
                shutdown_checker,
            )
            if isinstance(result, ExecutionResult):
                return result
            ctx = result

            # Stagnation detection after successful turn processing
            stag_outcome = await check_stagnation(
                ctx,
                self._stagnation_detector,
                turns,
                corrections_injected,
                execution_id=ctx.execution_id,
            )
            if isinstance(stag_outcome, ExecutionResult):
                return stag_outcome
            if isinstance(stag_outcome, tuple):
                ctx, corrections_injected = stag_outcome

            # Context compaction at turn boundaries
            compacted = await invoke_compaction(
                ctx,
                self._compaction_callback,
                turn_number,
            )
            if compacted is not None:
                ctx = compacted

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.MAX_TURNS.value,
            turns=len(turns),
        )
        return build_result(ctx, TerminationReason.MAX_TURNS, turns)

    def _prepare_loop(
        self,
        context: AgentContext,
        completion_config: CompletionConfig | None,
        tool_invoker: ToolInvokerProtocol | None,
    ) -> tuple[str, CompletionConfig, list[ToolDefinition] | None, list[TurnRecord]]:
        """Log loop start and resolve config, model ID, and tool defs.

        Returns:
            ``(model_id, config, tool_defs, turns)``: the resolved
            model id, the effective :class:`CompletionConfig`, the
            tool definitions for the loop (``None`` when no invoker),
            and an empty turn-record list to accumulate into.
        """
        logger.info(
            EXECUTION_LOOP_START,
            execution_id=context.execution_id,
            loop_type=self.get_loop_type(),
            max_turns=context.max_turns,
        )
        model_id = context.identity.model.model_id
        config = completion_config or CompletionConfig(
            temperature=context.identity.model.temperature,
            max_tokens=context.identity.model.max_tokens,
        )
        return (
            model_id,
            config,
            get_tool_definitions(tool_invoker, context.loaded_tools),
            [],
        )

    async def _process_turn_response(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
        tool_invoker: ToolInvokerProtocol | None,
        shutdown_checker: ShutdownChecker | None = None,
    ) -> AgentContext | ExecutionResult:
        """Check errors, update context, handle completion or tool calls.

        Returns:
            The updated :class:`AgentContext` when the loop should
            continue, or an :class:`ExecutionResult` when this turn
            terminates the loop (completion, error, shutdown, or
            tool-execution outcome).
        """
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

        # Checkpoint is saved after the LLM response is recorded but
        # before tool execution.  This is intentional: if a crash
        # happens during tool execution, the agent resumes with the
        # LLM response and can detect whether tools already ran.  The
        # alternative (after tools) would lose the entire LLM call on
        # a mid-tool crash.  Tools should be idempotent by design.
        if self._checkpoint_callback is not None:
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

        if not response.tool_calls:
            return self._handle_completion(ctx, response, turns)

        # Check shutdown before tool invocations
        shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
        if shutdown_result is not None:
            clear_last_turn_tool_calls(turns)
            # Rebuild with cleaned turns (shutdown_result snapshot'd old turns)
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

    def _handle_completion(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turns: list[TurnRecord],
    ) -> ExecutionResult:
        """Handle no-tool-call responses: normal completion or TOOL_USE error.

        Returns:
            An :class:`ExecutionResult` with
            ``termination_reason=ERROR`` for the malformed
            ``TOOL_USE``-without-tools case, or ``COMPLETED`` for the
            normal text-response completion.
        """
        if response.finish_reason == FinishReason.TOOL_USE:
            error_msg = (
                "Provider returned TOOL_USE with no tool calls "
                f"on turn {ctx.turn_count}"
            )
            logger.error(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=ctx.turn_count,
                error=error_msg,
            )
            return build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )
        if response.finish_reason == FinishReason.MAX_TOKENS:
            logger.warning(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.COMPLETED.value,
                turns=len(turns),
                truncated=True,
            )
        else:
            logger.info(
                EXECUTION_LOOP_TERMINATED,
                execution_id=ctx.execution_id,
                reason=TerminationReason.COMPLETED.value,
                turns=len(turns),
            )
        return build_result(
            ctx,
            TerminationReason.COMPLETED,
            turns,
        )
