"""Shared plan utilities for plan-based execution loops.

Stateless helpers used by both ``PlanExecuteLoop`` and ``HybridLoop``
for common plan-step operations, plus the one planner turn both drive:
``run_planner_turn`` owns the guard, the provider call, the turn record,
and the checkpoint, leaving each loop only its own prompt and parse.
"""

from typing import Final

from synthorg.core.completion_enums import FinishReason
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    classify_turn,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.plan_loop_context import (
    ReplanTrigger,
    StepRunContext,
    StepRunState,
)
from synthorg.engine.plan_models import ExecutionPlan, StepStatus
from synthorg.engine.plan_parsing import parse_plan
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import STEERING_REPLAN_SUPERSEDED
from synthorg.observability.events.execution import (
    EXECUTION_CHECKPOINT_CALLBACK_FAILED,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_PLAN_PARSE_ERROR,
    EXECUTION_PLAN_STEP_INDEX_OUT_OF_RANGE,
    EXECUTION_PLAN_STEP_STATUS_UPDATED,
    EXECUTION_PLAN_SUMMARY_FALLBACK,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse

logger = get_logger(__name__)

_MAX_TASK_SUMMARY_LENGTH: Final[int] = 200
"""Internal constant by design: maximum character length for task
summary strings; defensive truncation prevents bloated summaries.
Not exposed to the settings registry."""


def update_step_status(
    plan: ExecutionPlan,
    step_idx: int,
    status: StepStatus,
) -> ExecutionPlan:
    """Return a new plan with the given step's status updated.

    Args:
        plan: The current execution plan (frozen).
        step_idx: Zero-based index of the step to update.
        status: New status for the step.

    Returns:
        A copy of *plan* with the step at *step_idx* updated.

    Raises:
        IndexError: If *step_idx* is out of range.
    """
    if step_idx < 0 or step_idx >= len(plan.steps):
        step_count = len(plan.steps)
        logger.warning(
            EXECUTION_PLAN_STEP_INDEX_OUT_OF_RANGE,
            step_idx=step_idx,
            step_count=step_count,
            revision=plan.revision_number,
        )
        msg = (
            f"step_idx {step_idx} out of range for plan with "
            f"{step_count} steps (revision {plan.revision_number})"
        )
        raise IndexError(msg)
    steps = list(plan.steps)
    from_status = steps[step_idx].status
    steps[step_idx] = steps[step_idx].model_copy(
        update={"status": status},
    )
    logger.info(
        EXECUTION_PLAN_STEP_STATUS_UPDATED,
        step_idx=step_idx,
        from_status=from_status.value,
        to_status=status.value,
        revision=plan.revision_number,
    )
    return plan.model_copy(update={"steps": tuple(steps)})


def extract_task_summary(ctx: AgentContext) -> str:
    """Extract a task summary from the context.

    Uses the task title when available, otherwise the first user
    message.  Truncates to 200 characters.

    Args:
        ctx: Agent context to extract from.

    Returns:
        A short summary string.
    """
    if ctx.task_execution is not None:
        return ctx.task_execution.task.title[:_MAX_TASK_SUMMARY_LENGTH]
    for msg in ctx.conversation:
        if msg.role == MessageRole.USER and msg.content:
            return msg.content[:_MAX_TASK_SUMMARY_LENGTH]
    logger.warning(
        EXECUTION_PLAN_SUMMARY_FALLBACK,
        execution_id=ctx.execution_id,
        note="No task_execution or user messages; using default summary",
    )
    return "task"


def assess_step_success(response: CompletionResponse) -> bool:
    """Determine if a step completed successfully.

    A step is considered successful when the LLM terminates
    normally (STOP or MAX_TOKENS).  MAX_TOKENS is treated as
    success because the step instruction asks the LLM to summarize
    its work; a truncated summary still represents a completed
    step for planning purposes.

    Args:
        response: The LLM completion response for the step.

    Returns:
        ``True`` when the step is considered successful.
    """
    return response.finish_reason in (
        FinishReason.STOP,
        FinishReason.MAX_TOKENS,
    )


def clear_superseded_directive(
    state: StepRunState,
    *,
    trigger: ReplanTrigger,
) -> AgentContext:
    """Clear a pending steering directive a replan has already absorbed.

    The replan that just ran was prompted with the adopted directive already
    in the conversation, so a dedicated steering replan at the next boundary
    would only repeat it. Left set, the flag would instead fire a stale
    replan on a later step or persist into a terminal checkpoint.

    Args:
        state: Run state whose context carries the pending directive.
        trigger: What prompted the replan that superseded the directive.

    Returns:
        The context with the pending-replan id cleared, or ``state.ctx``
        unchanged when nothing was pending.
    """
    directive_id = state.ctx.pending_steering_replan_id
    if directive_id is None:
        return state.ctx
    logger.info(
        STEERING_REPLAN_SUPERSEDED,
        execution_id=state.ctx.execution_id,
        directive_id=directive_id,
        step_index=state.step_idx,
        trigger=trigger.value,
    )
    return state.ctx.cleared_pending_replan()


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
        # lint-allow: swallow-ok -- resiliency side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_CHECKPOINT_CALLBACK_FAILED,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def call_planner(
    run: StepRunContext,
    ctx: AgentContext,
    turns: list[TurnRecord],
    message: ChatMessage,
    *,
    revision_number: int = 0,
) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
    """Send one planner message and parse the plan it returned.

    The one planner call both loops make. Takes ``ctx`` and ``turns``
    explicitly rather than a :class:`StepRunState`, because initial
    planning runs before a plan exists and therefore before the state
    object can be built.

    Args:
        run: Run-scoped collaborators; the planner model, completion
            config, and checkpoint callback are read from here.
        ctx: Agent context.
        turns: Mutable list of turn records.
        message: The planning message to send.
        revision_number: Plan revision number.

    Returns:
        ``(ctx, plan)`` on success, or ``ExecutionResult`` on error.
    """
    task_summary = extract_task_summary(ctx)
    outcome = await run_planner_turn(run, ctx, turns, message)
    if isinstance(outcome, ExecutionResult):
        return outcome
    ctx, response = outcome

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


async def run_planner_turn(
    run: StepRunContext,
    ctx: AgentContext,
    turns: list[TurnRecord],
    message: ChatMessage,
) -> tuple[AgentContext, CompletionResponse] | ExecutionResult:
    """Send one planner message and record the turn it produced.

    Both loops drive the planner the same way, so the guard, the provider
    call, the turn record, the error check, and the checkpoint live here
    once; each caller keeps only its own prompt and its own parse.

    ``turns`` is mutated in place and the advanced context is returned, so
    the two are one unit: a caller that drops the returned ``ctx`` leaves a
    recorded turn whose effects never landed.

    Args:
        run: Run-scoped collaborators; the planner model, completion
            config, and checkpoint callback are read from here.
        ctx: Agent context to advance across the planner turn.
        turns: Mutable accumulator the turn record is appended to.
        message: The planning message to send.

    Returns:
        ``(advanced_ctx, response)`` on success, or a terminal
        :class:`ExecutionResult` when the turn budget is spent or the
        provider call failed.
    """
    if not ctx.has_turns_remaining:
        return build_result(ctx, TerminationReason.MAX_TURNS, turns)

    ctx = ctx.with_message(message)
    turn_number = ctx.turn_count + 1

    response = await call_provider(
        ctx,
        run.provider,
        run.planner_model,
        tool_defs=None,
        config=run.completion_config,
        turn_number=turn_number,
        turns=turns,
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

    await invoke_checkpoint_callback(run.checkpoint_callback, ctx, turn_number)
    return ctx, response
