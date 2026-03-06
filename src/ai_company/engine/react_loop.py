"""ReAct execution loop — think, act, observe.

Implements the ``ExecutionLoop`` protocol using the ReAct pattern:
call LLM -> check termination -> execute tools -> feed results -> repeat.
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

    The loop calls the LLM, checks for termination conditions, executes
    any requested tools, feeds results back, and repeats until the LLM
    signals completion, the turn limit is reached, the budget is
    exhausted, or an error occurs.
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

        Args:
            context: Initial agent context with conversation.
            provider: LLM completion provider.
            tool_invoker: Optional tool invoker for tool execution.
            budget_checker: Optional budget exhaustion callback.
            completion_config: Optional per-execution config override.

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
        total_tool_calls = 0
        ctx = context

        while ctx.has_turns_remaining:
            # Budget check before each LLM call
            if budget_checker is not None and budget_checker(ctx):
                logger.warning(
                    EXECUTION_LOOP_BUDGET_EXHAUSTED,
                    execution_id=ctx.execution_id,
                    turn=ctx.turn_count,
                )
                return _build_result(
                    ctx,
                    TerminationReason.BUDGET_EXHAUSTED,
                    turns,
                    total_tool_calls,
                )

            turn_number = ctx.turn_count + 1
            logger.debug(
                EXECUTION_LOOP_TURN_START,
                execution_id=ctx.execution_id,
                turn=turn_number,
            )

            response = await provider.complete(
                messages=list(ctx.conversation),
                model=model_id,
                tools=tool_defs,
                config=config,
            )

            turn_record = TurnRecord(
                turn_number=turn_number,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cost_usd=response.usage.cost_usd,
                tool_calls_made=tuple(tc.name for tc in response.tool_calls),
                finish_reason=response.finish_reason,
            )
            turns.append(turn_record)

            # Error responses may lack content and tool_calls, so handle
            # them before constructing the assistant ChatMessage.
            if response.finish_reason in (
                FinishReason.CONTENT_FILTER,
                FinishReason.ERROR,
            ):
                error_msg = (
                    f"LLM returned {response.finish_reason.value} on turn {turn_number}"
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
                    total_tool_calls,
                    error_message=error_msg,
                )

            assistant_msg = _response_to_message(response)
            ctx = ctx.with_turn_completed(response.usage, assistant_msg)

            logger.info(
                EXECUTION_LOOP_TURN_COMPLETE,
                execution_id=ctx.execution_id,
                turn=turn_number,
                finish_reason=response.finish_reason.value,
                tool_call_count=len(response.tool_calls),
            )

            if not response.tool_calls:
                # STOP or MAX_TOKENS with no tool calls -> completed
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
                    total_tool_calls,
                )

            # Execute tool calls
            if tool_invoker is None:
                error_msg = (
                    f"LLM requested {len(response.tool_calls)} tool call(s) "
                    f"but no tool invoker is available"
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
                    total_tool_calls,
                    error_message=error_msg,
                )

            tool_names = [tc.name for tc in response.tool_calls]
            logger.info(
                EXECUTION_LOOP_TOOL_CALLS,
                execution_id=ctx.execution_id,
                turn=turn_number,
                tools=tool_names,
            )

            results = await tool_invoker.invoke_all(response.tool_calls)
            total_tool_calls += len(results)

            for result in results:
                tool_msg = ChatMessage(
                    role=MessageRole.TOOL,
                    tool_result=result,
                )
                ctx = ctx.with_message(tool_msg)

        # Loop exited: max turns reached
        logger.info(
            EXECUTION_LOOP_TERMINATED,
            execution_id=ctx.execution_id,
            reason=TerminationReason.MAX_TURNS.value,
            turns=len(turns),
        )
        return _build_result(
            ctx,
            TerminationReason.MAX_TURNS,
            turns,
            total_tool_calls,
        )


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


def _build_result(
    ctx: AgentContext,
    reason: TerminationReason,
    turns: list[TurnRecord],
    total_tool_calls: int,
    *,
    error_message: str | None = None,
) -> ExecutionResult:
    """Build an ``ExecutionResult`` from loop state."""
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        turns=tuple(turns),
        total_tool_calls=total_tool_calls,
        error_message=error_message,
    )
