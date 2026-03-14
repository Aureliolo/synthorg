"""Shared stateless helpers for all ExecutionLoop implementations.

Each function operates on explicit parameters (no ``self``), keeping
loop implementations (ReAct, Plan-and-Execute, etc.) thin and focused
on their control-flow logic.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PARK_TASKLESS,
)
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_BUDGET_EXHAUSTED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_SHUTDOWN,
    EXECUTION_LOOP_TOOL_CALLS,
    EXECUTION_LOOP_TURN_START,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
    add_token_usage,
)

from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
    TurnRecord,
)

if TYPE_CHECKING:
    from synthorg.budget.call_category import LLMCallCategory
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.approval_gate_models import EscalationInfo
    from synthorg.engine.context import AgentContext
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.invoker import ToolInvoker

logger = get_logger(__name__)


def check_shutdown(
    ctx: AgentContext,
    shutdown_checker: ShutdownChecker | None,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return a SHUTDOWN result if a shutdown has been requested.

    Args:
        ctx: Current agent context.
        shutdown_checker: Optional callback returning ``True`` on shutdown.
        turns: Accumulated turn records.

    Returns:
        ``ExecutionResult`` with SHUTDOWN reason, or ``None`` to continue.
    """
    if shutdown_checker is None:
        return None
    try:
        shutting_down = shutdown_checker()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        error_msg = f"Shutdown checker failed: {type(exc).__name__}: {exc}"
        logger.exception(
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
    if not shutting_down:
        return None
    logger.info(
        EXECUTION_LOOP_SHUTDOWN,
        execution_id=ctx.execution_id,
        turn=ctx.turn_count,
    )
    return build_result(ctx, TerminationReason.SHUTDOWN, turns)


def check_budget(
    ctx: AgentContext,
    budget_checker: BudgetChecker | None,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return a BUDGET_EXHAUSTED result if budget is exhausted.

    Args:
        ctx: Current agent context.
        budget_checker: Optional callback returning ``True`` on exhaustion.
        turns: Accumulated turn records.

    Returns:
        ``ExecutionResult`` with BUDGET_EXHAUSTED reason, or ``None``.
    """
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
        return build_result(
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
        return build_result(
            ctx,
            TerminationReason.BUDGET_EXHAUSTED,
            turns,
        )
    return None


async def call_provider(  # noqa: PLR0913
    ctx: AgentContext,
    provider: CompletionProvider,
    model_id: str,
    tool_defs: list[ToolDefinition] | None,
    config: CompletionConfig,
    turn_number: int,
    turns: list[TurnRecord],
) -> CompletionResponse | ExecutionResult:
    """Call ``provider.complete()``, returning an error result on failure.

    Args:
        ctx: Current agent context with conversation history.
        provider: LLM completion provider.
        model_id: Model identifier to use.
        tool_defs: Optional tool definitions to pass to the LLM.
        config: Completion config (temperature, max_tokens, etc.).
        turn_number: Current turn number (1-indexed).
        turns: Accumulated turn records.

    Returns:
        ``CompletionResponse`` on success, or ``ExecutionResult`` on error.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    char_count = sum(len(m.content or "") for m in ctx.conversation)
    logger.info(
        EXECUTION_LOOP_TURN_START,
        execution_id=ctx.execution_id,
        turn=turn_number,
        message_count=len(ctx.conversation),
        char_count_estimate=char_count,
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
        error_msg = f"Provider error on turn {turn_number}: {type(exc).__name__}: {exc}"
        logger.exception(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error=error_msg,
        )
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )


def check_response_errors(
    ctx: AgentContext,
    response: CompletionResponse,
    turn_number: int,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return an error result for CONTENT_FILTER or ERROR responses.

    When returning an error result, the result's context includes the
    failing turn's token usage so callers see accurate totals.
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
            "accumulated_cost": add_token_usage(ctx.accumulated_cost, response.usage),
        },
    )
    return build_result(
        updated_ctx,
        TerminationReason.ERROR,
        turns,
        error_message=error_msg,
    )


async def execute_tool_calls(  # noqa: PLR0913
    ctx: AgentContext,
    tool_invoker: ToolInvoker | None,
    response: CompletionResponse,
    turn_number: int,
    turns: list[TurnRecord],
    *,
    approval_gate: ApprovalGate | None = None,
) -> AgentContext | ExecutionResult:
    """Execute tool calls and append results to context.

    When an ``approval_gate`` is provided and the invoker reports
    pending escalations, the context is parked and a PARKED result
    is returned.

    Args:
        ctx: Current agent context.
        tool_invoker: Tool invoker (``None`` causes an error result).
        response: Provider response containing tool calls.
        turn_number: Current turn number (1-indexed).
        turns: Accumulated turn records.
        approval_gate: Optional approval gate for escalation parking.

    Returns:
        Updated ``AgentContext`` on success, or ``ExecutionResult`` on error.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
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
        # Clear tool_calls on the turn record — tools were never executed
        clear_last_turn_tool_calls(turns)
        return build_result(
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
            f"Tool execution failed on turn {turn_number}: {type(exc).__name__}: {exc}"
        )
        logger.exception(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error=error_msg,
            tools=tool_names,
        )
        return build_result(
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

    # Check for escalations requiring parking.
    if approval_gate is not None:
        escalation = approval_gate.should_park(
            tool_invoker.pending_escalations,
        )
        if escalation is not None:
            return await _park_for_approval(
                ctx,
                escalation,
                approval_gate,
                turns,
            )

    return ctx


async def _park_for_approval(
    ctx: AgentContext,
    escalation: EscalationInfo,
    approval_gate: ApprovalGate,
    turns: list[TurnRecord],
) -> ExecutionResult:
    """Park the context for approval and return a PARKED or ERROR result.

    On success, returns PARKED with the approval_id in metadata.
    On failure (serialization/persistence error), returns ERROR — the
    agent should not continue, and the caller should treat this as a
    non-resumable failure.

    Args:
        ctx: Current agent context.
        escalation: The escalation that triggered parking.
        approval_gate: The approval gate service.
        turns: Accumulated turn records.

    Returns:
        An ``ExecutionResult`` with PARKED or ERROR termination reason.
    """
    agent_id = str(ctx.identity.id)
    task_id: str | None = None
    if ctx.task_execution is not None:
        task_id = ctx.task_execution.task.id
    else:
        logger.debug(
            APPROVAL_GATE_PARK_TASKLESS,
            approval_id=escalation.approval_id,
            agent_id=agent_id,
            note="No task_execution on context — task_id will be None",
        )

    try:
        await approval_gate.park_context(
            escalation=escalation,
            context=ctx,
            agent_id=agent_id,
            task_id=task_id,
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        # ApprovalGate already logs APPROVAL_GATE_CONTEXT_PARK_FAILED
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=(
                f"Approval escalation detected (id={escalation.approval_id}) "
                f"but context parking failed — cannot resume"
            ),
            metadata={
                "approval_id": escalation.approval_id,
                "parking_failed": True,
            },
        )

    return build_result(
        ctx,
        TerminationReason.PARKED,
        turns,
        metadata={
            "approval_id": escalation.approval_id,
            "parking_failed": False,
        },
    )


def clear_last_turn_tool_calls(turns: list[TurnRecord]) -> None:
    """Clear tool_calls_made on the last TurnRecord.

    Used when shutdown fires between recording a turn and executing
    tools — the turn should not overstate what happened.

    Args:
        turns: Mutable list of turn records (modified in-place).
    """
    if turns:
        last = turns[-1]
        turns[-1] = last.model_copy(update={"tool_calls_made": ()})


def get_tool_definitions(
    tool_invoker: ToolInvoker | None,
) -> list[ToolDefinition] | None:
    """Extract permitted tool definitions from the invoker, or return None."""
    if tool_invoker is None:
        return None
    defs = tool_invoker.get_permitted_definitions()
    return list(defs) if defs else None


def response_to_message(response: CompletionResponse) -> ChatMessage:
    """Convert a ``CompletionResponse`` to an assistant ``ChatMessage``."""
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=response.content,
        tool_calls=response.tool_calls,
    )


def make_turn_record(
    turn_number: int,
    response: CompletionResponse,
    *,
    call_category: LLMCallCategory | None = None,
) -> TurnRecord:
    """Create a ``TurnRecord`` from a provider response."""
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=response.usage.cost_usd,
        tool_calls_made=tuple(tc.name for tc in response.tool_calls),
        finish_reason=response.finish_reason,
        call_category=call_category,
    )


def build_result(
    ctx: AgentContext,
    reason: TerminationReason,
    turns: list[TurnRecord],
    *,
    error_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    """Build an ``ExecutionResult`` from loop state."""
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        turns=tuple(turns),
        error_message=error_message,
        metadata=metadata or {},
    )
