"""Step-execution helpers for the Hybrid Plan + ReAct loop.

Covers plan truncation, per-step instruction-message assembly,
turn-completion handling, budget warnings, checkpoint dispatch, and
the shared planner-call body used by both initial planning and
re-planning. Stateless free functions only; no instance state.
"""

from typing import TYPE_CHECKING

from synthorg.core.completion_enums import FinishReason
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    classify_turn,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.plan_helpers import (
    assess_step_success,
    extract_task_summary,
)
from synthorg.engine.plan_models import ExecutionPlan, PlanStep
from synthorg.engine.plan_parsing import parse_plan
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
    EXECUTION_HYBRID_PLAN_TRUNCATED,
    EXECUTION_HYBRID_TURN_BUDGET_WARNING,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_PARSE_ERROR,
    EXECUTION_PLAN_STEP_TRUNCATED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig, CompletionResponse
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    # ``checkpoint`` package init eagerly pulls ``resume`` -> ``hybrid_loop``;
    # a runtime import here would cycle back into the mid-import hybrid loop.
    from synthorg.engine.checkpoint.callback import CheckpointCallback

logger = get_logger(__name__)


def truncate_plan(
    plan: ExecutionPlan,
    max_steps: int,
) -> ExecutionPlan:
    """Truncate plan to *max_steps* if it exceeds the limit.

    The run id for the truncation log is read from the ambient
    ``current_execution_identity()`` (bound at the engine run boundary)
    rather than threaded in as a parameter.

    Args:
        plan: The execution plan to potentially truncate.
        max_steps: Maximum allowed number of steps.

    Returns:
        The original plan if within limit, otherwise a truncated copy.
    """
    if len(plan.steps) <= max_steps:
        return plan
    identity = current_execution_identity()
    logger.warning(
        EXECUTION_HYBRID_PLAN_TRUNCATED,
        execution_id=identity.execution_id if identity is not None else None,
        original_steps=len(plan.steps),
        truncated_to=max_steps,
    )
    truncated_steps = tuple(
        step.model_copy(update={"step_number": i + 1})
        for i, step in enumerate(plan.steps[:max_steps])
    )
    return plan.model_copy(update={"steps": truncated_steps})


def build_step_message(step: PlanStep) -> ChatMessage:
    """Build the instruction message for a plan step.

    Args:
        step: The plan step to build a message for.

    Returns:
        A chat message instructing the LLM to execute the step.
    """
    safe_desc = wrap_untrusted(TAG_TASK_DATA, step.description)
    safe_outcome = wrap_untrusted(TAG_TASK_DATA, step.expected_outcome)
    instruction = (
        f"Execute the following step {step.step_number}:\n"
        f"Description:\n{safe_desc}\n"
        f"Expected outcome:\n{safe_outcome}\n"
        f"{untrusted_content_directive((TAG_TASK_DATA,))} When done, respond "
        f"with a summary of what you accomplished."
    )
    return ChatMessage(
        role=MessageRole.USER,
        content=instruction,
    )


def handle_step_completion(
    ctx: AgentContext,
    response: CompletionResponse,
    turn_number: int,
) -> tuple[AgentContext, bool]:
    """Assess step success and log truncation if applicable.

    Args:
        ctx: Agent context.
        response: LLM completion response for the step.
        turn_number: Current turn number for logging.

    Returns:
        ``(ctx, success)`` where *success* indicates step completion.
    """
    if response.finish_reason == FinishReason.TOOL_USE:
        logger.error(
            EXECUTION_LOOP_TURN_COMPLETE,
            execution_id=ctx.execution_id,
            turn=turn_number,
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


def warn_insufficient_budget(
    config: HybridLoopConfig,
    ctx: AgentContext,
) -> None:
    """Log a warning if the turn budget is likely insufficient.

    Args:
        config: Hybrid loop configuration.
        ctx: Agent context with turn budget information.
    """
    # plan(1) + steps * (turns + summary(1)) -- excludes replan overhead
    estimated_min = 1 + config.max_plan_steps * (
        config.max_turns_per_step + (1 if config.checkpoint_after_each_step else 0)
    )
    if estimated_min > ctx.max_turns:
        logger.warning(
            EXECUTION_HYBRID_TURN_BUDGET_WARNING,
            execution_id=ctx.execution_id,
            estimated_min_turns=estimated_min,
            max_turns=ctx.max_turns,
            max_plan_steps=config.max_plan_steps,
            max_turns_per_step=config.max_turns_per_step,
        )


async def invoke_checkpoint_callback(
    callback: CheckpointCallback | None,
    ctx: AgentContext,
    turn_number: int,
) -> None:
    """Invoke the checkpoint callback if provided.

    Non-critical errors are logged and swallowed so checkpointing does
    not interrupt execution. Critical system errors are propagated.

    Args:
        callback: Optional checkpoint callback to invoke.
        ctx: Agent context for the current turn.
        turn_number: Current turn number for logging.

    Raises:
        MemoryError: Propagated as a critical system error.
        RecursionError: Propagated as a critical system error.
    """
    if callback is None:
        return
    try:
        await callback(ctx)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EXECUTION_CHECKPOINT_CALLBACK_FAILED,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def call_planner(  # noqa: PLR0913
    ctx: AgentContext,
    provider: CompletionProvider,
    model: str,
    config: CompletionConfig,
    turns: list[TurnRecord],
    message: ChatMessage,
    *,
    revision_number: int = 0,
    checkpoint_callback: CheckpointCallback | None = None,
) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
    """Shared body for plan generation and re-planning.

    Args:
        ctx: Agent context.
        provider: LLM completion provider.
        model: Model ID to use for the call.
        config: Completion configuration.
        turns: Mutable list of turn records.
        message: The planning message to send.
        revision_number: Plan revision number.
        checkpoint_callback: Optional checkpoint callback.

    Returns:
        ``(ctx, plan)`` on success, or ``ExecutionResult`` on error.
    """
    if not ctx.has_turns_remaining:
        return build_result(ctx, TerminationReason.MAX_TURNS, turns)

    task_summary = extract_task_summary(ctx)
    ctx = ctx.with_message(message)
    turn_number = ctx.turn_count + 1

    response = await call_provider(
        ctx, provider, model, None, config, turn_number, turns
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

    await invoke_checkpoint_callback(checkpoint_callback, ctx, turn_number)

    plan = parse_plan(
        response,
        ctx.execution_id,
        task_summary,
        revision_number=revision_number,
    )
    if plan is None:
        error_msg = "Failed to parse execution plan from LLM response"
        logger.warning(
            EXECUTION_PLAN_PARSE_ERROR,
            execution_id=ctx.execution_id,
            revision_number=revision_number,
        )
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )
    return ctx, plan
