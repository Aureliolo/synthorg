"""ReAct execution loop — think, act, observe.

Implements the ``ExecutionLoop`` protocol using the ReAct pattern:
check budget -> call LLM -> record turn -> check for LLM errors ->
update context -> handle completion or execute tools -> repeat.
"""

from typing import TYPE_CHECKING

from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_LOOP_BUDGET_EXHAUSTED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_START,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TOOL_CALLS,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_LOOP_TURN_START,
)
from ai_company.providers.enums import FinishReason, MessageRole
from ai_company.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
    add_token_usage,
)

from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)

if TYPE_CHECKING:
    from ai_company.engine.context import AgentContext
    from ai_company.providers.protocol import CompletionProvider
    from ai_company.tools.invoker import ToolInvoker

logger = get_logger(__name__)


class ReactLoop:
    """ReAct execution loop: reason, act, observe.

    The loop checks the budget, calls the LLM, checks for termination
    conditions, executes any requested tools, feeds results back, and
    repeats until the LLM signals completion, the turn limit is reached,
    the budget is exhausted, or an error occurs.
    """

    def get_loop_type(self) -> str:
        """Return the loop type identifier."""
        return "react"

    async def execute(
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvoker | None = None,
        budget_checker: BudgetChecker | None = None,
        completion_config: CompletionConfig | None = None,
    ) -> ExecutionResult:
        """Run the ReAct loop until termination.

        Normal failure modes (budget exhaustion, LLM errors, provider
        failures, missing tool invoker) are returned as
        ``ExecutionResult`` with the appropriate ``TerminationReason``
        rather than raised as exceptions.  Non-recoverable errors
        (``MemoryError``, ``RecursionError``) are re-raised rather
        than captured in the result.

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional budget exhaustion callback.
            completion_config: Optional per-execution config override.
                Implementations may fall back to the identity's model
                config when not provided.

        Returns:
            Execution result with final context and termination info.
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
        tool_defs = _get_tool_definitions(tool_invoker)
        turns: list[TurnRecord] = []
        ctx = context

        while ctx.has_turns_remaining:
            budget_result = self._check_budget(
                ctx,
                budget_checker,
                turns,
            )
            if budget_result is not None:
                return budget_result

            turn_number = ctx.turn_count + 1
            response = await self._call_provider(
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

            turns.append(_make_turn_record(turn_number, response))

            error = self._check_response_errors(
                ctx,
                response,
                turn_number,
                turns,
            )
            if error is not None:
                return error

            ctx = ctx.with_turn_completed(
                response.usage,
                _response_to_message(response),
            )
            logger.info(
                EXECUTION_LOOP_TURN_COMPLETE,
                execution_id=ctx.execution_id,
                turn=turn_number,
                finish_reason=response.finish_reason.value,
                tool_call_count=len(response.tool_calls),
            )

            if not response.tool_calls:
                return self._handle_completion(
                    ctx,
                    response,
                    turns,
                )

            ctx_or_err = await self._execute_tool_calls(
                ctx,
                tool_invoker,
                response,
                turn_number,
                turns,
            )
            if isinstance(ctx_or_err, ExecutionResult):
                return ctx_or_err
            ctx = ctx_or_err

        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.MAX_TURNS.value,
            turns=len(turns),
        )
        return _build_result(ctx, TerminationReason.MAX_TURNS, turns)

    def _check_budget(
        self,
        ctx: AgentContext,
        budget_checker: BudgetChecker | None,
        turns: list[TurnRecord],
    ) -> ExecutionResult | None:
        """Return a termination result if budget is exhausted or checker raises."""
        if budget_checker is None:
            return None
        try:
            exhausted = budget_checker(ctx)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_msg = f"Budget checker failed: {type(exc).__name__}: {exc}"
            logger.exception(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=ctx.turn_count,
                error=error_msg,
            )
            return _build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )
        if exhausted:
            logger.warning(
                EXECUTION_LOOP_BUDGET_EXHAUSTED,
                execution_id=ctx.execution_id,
                turn=ctx.turn_count,
            )
            return _build_result(
                ctx,
                TerminationReason.BUDGET_EXHAUSTED,
                turns,
            )
        return None

    async def _call_provider(  # noqa: PLR0913
        self,
        ctx: AgentContext,
        provider: CompletionProvider,
        model_id: str,
        tool_defs: list[ToolDefinition] | None,
        config: CompletionConfig,
        turn_number: int,
        turns: list[TurnRecord],
    ) -> CompletionResponse | ExecutionResult:
        """Call provider.complete(), returning an error result on failure."""
        logger.debug(
            EXECUTION_LOOP_TURN_START,
            execution_id=ctx.execution_id,
            turn=turn_number,
        )
        try:
            return await provider.complete(
                messages=list(ctx.conversation),
                model=model_id,
                tools=tool_defs,
                config=config,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_msg = (
                f"Provider error on turn {turn_number}: {type(exc).__name__}: {exc}"
            )
            logger.exception(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=turn_number,
                error=error_msg,
            )
            return _build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )

    def _check_response_errors(
        self,
        ctx: AgentContext,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
    ) -> ExecutionResult | None:
        """Return an error result for CONTENT_FILTER or ERROR responses.

        The context's accumulated cost is updated to include the failing
        turn's token usage so callers see accurate totals.
        """
        if response.finish_reason not in (
            FinishReason.CONTENT_FILTER,
            FinishReason.ERROR,
        ):
            return None
        error_msg = f"LLM returned {response.finish_reason.value} on turn {turn_number}"
        logger.error(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error=error_msg,
        )
        updated_ctx = ctx.model_copy(
            update={
                "turn_count": ctx.turn_count + 1,
                "accumulated_cost": add_token_usage(
                    ctx.accumulated_cost, response.usage
                ),
            },
        )
        return _build_result(
            updated_ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
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
            return _build_result(
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
        return _build_result(
            ctx,
            TerminationReason.COMPLETED,
            turns,
        )

    async def _execute_tool_calls(
        self,
        ctx: AgentContext,
        tool_invoker: ToolInvoker | None,
        response: CompletionResponse,
        turn_number: int,
        turns: list[TurnRecord],
    ) -> AgentContext | ExecutionResult:
        """Execute tool calls and append results to context, or error if no invoker."""
        if tool_invoker is None:
            error_msg = (
                f"LLM requested {len(response.tool_calls)} tool "
                f"call(s) but no tool invoker is available"
            )
            logger.error(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=turn_number,
                error=error_msg,
            )
            return _build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )

        tool_names = [tc.name for tc in response.tool_calls]
        logger.info(
            EXECUTION_LOOP_TOOL_CALLS,
            execution_id=ctx.execution_id,
            turn=turn_number,
            tools=tool_names,
        )

        try:
            results = await tool_invoker.invoke_all(
                response.tool_calls,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_msg = (
                f"Tool execution failed on turn {turn_number}: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.exception(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=turn_number,
                error=error_msg,
                tools=tool_names,
            )
            return _build_result(
                ctx,
                TerminationReason.ERROR,
                turns,
                error_message=error_msg,
            )

        for result in results:
            tool_msg = ChatMessage(
                role=MessageRole.TOOL,
                tool_result=result,
            )
            ctx = ctx.with_message(tool_msg)

        return ctx


def _get_tool_definitions(
    tool_invoker: ToolInvoker | None,
) -> list[ToolDefinition] | None:
    """Extract tool definitions from the invoker, or return None."""
    if tool_invoker is None:
        return None
    defs = tool_invoker.registry.to_definitions()
    return list(defs) if defs else None


def _response_to_message(response: CompletionResponse) -> ChatMessage:
    """Convert a ``CompletionResponse`` to an assistant ``ChatMessage``."""
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=response.content,
        tool_calls=response.tool_calls,
    )


def _make_turn_record(
    turn_number: int,
    response: CompletionResponse,
) -> TurnRecord:
    """Create a ``TurnRecord`` from a provider response."""
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=response.usage.cost_usd,
        tool_calls_made=tuple(tc.name for tc in response.tool_calls),
        finish_reason=response.finish_reason,
    )


def _build_result(
    ctx: AgentContext,
    reason: TerminationReason,
    turns: list[TurnRecord],
    *,
    error_message: str | None = None,
) -> ExecutionResult:
    """Build an ``ExecutionResult`` from loop state."""
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        turns=tuple(turns),
        error_message=error_message,
    )
