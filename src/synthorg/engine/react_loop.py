"""ReAct execution loop — think, act, observe.

Implements the ``ExecutionLoop`` protocol using the ReAct pattern:
check shutdown -> check budget -> call LLM -> record turn ->
check for LLM errors -> update context -> handle completion or
(check shutdown -> execute tools) -> repeat.
"""

from typing import TYPE_CHECKING

from synthorg.budget.call_category import LLMCallCategory
from synthorg.observability import get_logger
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

from .loop_helpers import (
    build_result,
    call_provider,
    check_budget,
    check_response_errors,
    check_shutdown,
    clear_last_turn_tool_calls,
    execute_tool_calls,
    get_tool_definitions,
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

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.context import AgentContext
    from synthorg.providers.models import ToolDefinition
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.invoker import ToolInvoker

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
    """

    def __init__(
        self,
        checkpoint_callback: CheckpointCallback | None = None,
        *,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._checkpoint_callback = checkpoint_callback
        self._approval_gate = approval_gate

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "react"

    async def execute(  # noqa: PLR0913
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvoker | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
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

        while ctx.has_turns_remaining:
            shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
            if shutdown_result is not None:
                return shutdown_result

            budget_result = check_budget(ctx, budget_checker, turns)
            if budget_result is not None:
                return budget_result

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
                    call_category=LLMCallCategory.PRODUCTIVE,
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
        tool_invoker: ToolInvoker | None,
    ) -> tuple[str, CompletionConfig, list[ToolDefinition] | None, list[TurnRecord]]:
        """Log loop start and resolve config, model ID, and tool defs."""
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
        return model_id, config, get_tool_definitions(tool_invoker), []

    async def _process_turn_response(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
        tool_invoker: ToolInvoker | None,
        shutdown_checker: ShutdownChecker | None = None,
    ) -> AgentContext | ExecutionResult:
        """Check errors, update context, handle completion or tool calls."""
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.exception(
                    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
                    execution_id=ctx.execution_id,
                    turn=turn_number,
                    error=f"{type(exc).__name__}: {exc}",
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
        """Handle no-tool-call responses: normal completion or TOOL_USE error."""
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
