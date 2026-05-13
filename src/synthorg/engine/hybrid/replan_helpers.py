"""Progress-summary and replanning helpers for the Hybrid loop.

Owns the "completed step -> summarise + decide whether to replan ->
re-plan" half of the hybrid loop. Calls back into step-helpers for the
shared planner-call body.
"""

import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.core.normalization import compare_ci
from synthorg.engine.hybrid.step_helpers import (
    call_planner,
    invoke_checkpoint_callback,
    truncate_plan,
)
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
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.plan_helpers import update_step_status
from synthorg.engine.plan_models import ExecutionPlan, PlanStep, StepStatus
from synthorg.engine.plan_parsing import _REPLAN_JSON_EXAMPLE
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_HYBRID_PROGRESS_SUMMARY,
    EXECUTION_HYBRID_PROGRESS_SUMMARY_EMPTY,
    EXECUTION_HYBRID_REPLAN_PARSE_TRACE,
    EXECUTION_PLAN_REPLAN_COMPLETE,
    EXECUTION_PLAN_REPLAN_EXHAUSTED,
    EXECUTION_PLAN_REPLAN_START,
    EXECUTION_PLAN_STEP_FAILED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.context import AgentContext
    from synthorg.engine.hybrid_models import HybridLoopConfig
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

# Type alias for the finalize callback passed from the loop class.
_Finalize = Callable[[ExecutionResult, list[ExecutionPlan], int], ExecutionResult]


def _build_summary_prompt(
    plan: ExecutionPlan,
    step_idx: int,
    *,
    ask_replan: bool,
) -> str:
    """Build the progress-summary prompt for a completed step.

    Args:
        plan: Current execution plan.
        step_idx: Zero-based index of the completed step.
        ask_replan: Whether to ask the LLM about replanning.

    Returns:
        The prompt string for the progress summary.
    """
    step_status_lines = "\n".join(
        f"  Step {s.step_number}: {s.description} -> {s.status.value}"
        for s in plan.steps
    )
    remaining = len(plan.steps) - step_idx - 1
    prompt = (
        f"You completed step {step_idx + 1} of {len(plan.steps)}. "
        f"Plan status:\n{step_status_lines}\n\n"
        f"Provide a brief progress summary. "
    )
    if ask_replan and remaining > 0:
        prompt += (
            f"If the remaining {remaining} step(s) need adjustment "
            f"based on what you learned, respond with a JSON object "
            f'containing "replan": true. Otherwise "replan": false.'
            f'\nFormat: {{"summary": "...", "replan": true/false}}'
        )
    else:
        prompt += "Summarize what was accomplished."
    return prompt


def _parse_replan_decision(content: str) -> bool:
    """Extract replan decision from summary response.

    Tries JSON extraction first, then a regex-based text heuristic.
    Defaults to ``False`` on parse failure and logs a warning when
    both parsers fail on non-empty content.

    Args:
        content: Raw LLM response content.

    Returns:
        ``True`` if the LLM indicated replanning is needed.
    """
    stripped = content.strip()
    if not stripped:
        return False

    # Try JSON extraction (with optional markdown fence)
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    json_str = fence_match.group(1).strip() if fence_match else stripped

    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            raw = data.get("replan")
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                return compare_ci(raw, "true")
            # Non-bool, non-str, or missing: treat as no-replan
            return False
        logger.debug(
            EXECUTION_HYBRID_REPLAN_PARSE_TRACE,
            parser="json",
            note="parsed JSON is not a dict",
        )
    except json.JSONDecodeError:
        logger.debug(
            EXECUTION_HYBRID_REPLAN_PARSE_TRACE,
            parser="json",
            note="JSON parse failed, trying text heuristic",
        )

    # Regex-based text heuristic (tolerates whitespace variations)
    lower = content.lower()
    if re.search(r'"replan"\s*:\s*true', lower):
        return True

    # Both parsers failed on non-empty content
    if '"replan"' in lower:
        logger.warning(
            EXECUTION_HYBRID_REPLAN_PARSE_TRACE,
            parser="fallback",
            note="replan key found but value not parsed as true; "
            "defaulting to no replan",
            content_snippet=content[:200],
        )
    return False


async def run_progress_summary(  # noqa: PLR0913
    config: HybridLoopConfig,
    checkpoint_callback: CheckpointCallback | None,
    ctx: AgentContext,
    provider: CompletionProvider,
    planner_model: str,
    completion_config: CompletionConfig,
    plan: ExecutionPlan,
    step_idx: int,
    turns: list[TurnRecord],
    budget_checker: BudgetChecker | None,
    shutdown_checker: ShutdownChecker | None,
) -> tuple[AgentContext, bool] | ExecutionResult:
    """Produce a progress summary and determine if replanning is needed.

    Args:
        config: Hybrid loop configuration.
        checkpoint_callback: Optional checkpoint callback.
        ctx: Agent context.
        provider: LLM completion provider.
        planner_model: Model ID for the planner.
        completion_config: Completion configuration.
        plan: Current execution plan.
        step_idx: Zero-based index of the completed step.
        turns: Mutable list of turn records.
        budget_checker: Optional budget exhaustion callback.
        shutdown_checker: Optional shutdown callback.

    Returns:
        ``(ctx, should_replan)`` on success, or ``ExecutionResult``
        for termination conditions.
    """
    if not ctx.has_turns_remaining:
        return build_result(ctx, TerminationReason.MAX_TURNS, turns)

    shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
    if shutdown_result is not None:
        return shutdown_result
    budget_result = check_budget(ctx, budget_checker, turns)
    if budget_result is not None:
        return budget_result

    summary_msg = ChatMessage(
        role=MessageRole.USER,
        content=_build_summary_prompt(
            plan,
            step_idx,
            ask_replan=(
                config.allow_replan_on_completion and step_idx < len(plan.steps) - 1
            ),
        ),
    )
    ctx = ctx.with_message(summary_msg)
    turn_number = ctx.turn_count + 1

    response = await call_provider(
        ctx,
        provider,
        planner_model,
        None,
        completion_config,
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
        EXECUTION_HYBRID_PROGRESS_SUMMARY,
        execution_id=ctx.execution_id,
        turn=turn_number,
        step_completed=step_idx + 1,
    )

    await invoke_checkpoint_callback(checkpoint_callback, ctx, turn_number)

    raw_content = response.content or ""
    if not raw_content.strip():
        logger.warning(
            EXECUTION_HYBRID_PROGRESS_SUMMARY_EMPTY,
            execution_id=ctx.execution_id,
            note="empty progress summary response",
        )
    should_replan = _parse_replan_decision(raw_content)
    return ctx, should_replan


async def attempt_replan(  # noqa: PLR0913
    config: HybridLoopConfig,
    ctx: AgentContext,
    provider: CompletionProvider,
    planner_model: str,
    completion_config: CompletionConfig,
    plan: ExecutionPlan,
    step: PlanStep,
    step_idx: int,
    turns: list[TurnRecord],
    all_plans: list[ExecutionPlan],
    replans_used: int,
    budget_checker: BudgetChecker | None,
    shutdown_checker: ShutdownChecker | None,
    *,
    finalize: _Finalize,
    checkpoint_callback: CheckpointCallback | None = None,
) -> tuple[AgentContext, ExecutionPlan, int] | ExecutionResult:
    """Handle a failed step: mark it, check replan budget, replan.

    Args:
        config: Hybrid loop configuration.
        ctx: Agent context.
        provider: LLM completion provider.
        planner_model: Model ID for the planner.
        completion_config: Completion configuration.
        plan: Current execution plan.
        step: The failed step.
        step_idx: Zero-based index of the failed step.
        turns: Mutable list of turn records.
        all_plans: Mutable list of all plans generated so far.
        replans_used: Number of replans used so far.
        budget_checker: Optional budget exhaustion callback.
        shutdown_checker: Optional shutdown callback.
        finalize: Callable that attaches hybrid metadata to a result.
        checkpoint_callback: Optional checkpoint callback to thread
            to the replanning call.

    Returns:
        ``(ctx, new_plan, replans_used)`` on success, or
        ``ExecutionResult`` for termination conditions.
    """
    plan = update_step_status(plan, step_idx, StepStatus.FAILED)
    logger.warning(
        EXECUTION_PLAN_STEP_FAILED,
        execution_id=ctx.execution_id,
        step_number=step.step_number,
    )

    if replans_used >= config.max_replans:
        logger.error(
            EXECUTION_PLAN_REPLAN_EXHAUSTED,
            execution_id=ctx.execution_id,
            replans_used=replans_used,
            max_replans=config.max_replans,
        )
        error_msg = (
            f"Max replans ({config.max_replans}) exhausted "
            f"after step {step.step_number} failed"
        )
        return finalize(
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
        return finalize(
            build_result(ctx, TerminationReason.MAX_TURNS, turns),
            all_plans,
            replans_used,
        )

    shutdown_result = check_shutdown(ctx, shutdown_checker, turns)
    if shutdown_result is not None:
        return finalize(shutdown_result, all_plans, replans_used)
    budget_result = check_budget(ctx, budget_checker, turns)
    if budget_result is not None:
        return finalize(budget_result, all_plans, replans_used)

    replan_result = await do_replan(
        config,
        ctx,
        provider,
        planner_model,
        completion_config,
        plan,
        step,
        turns,
        checkpoint_callback=checkpoint_callback,
    )
    if isinstance(replan_result, ExecutionResult):
        return finalize(replan_result, all_plans, replans_used)

    ctx, new_plan = replan_result
    replans_used += 1
    all_plans.append(new_plan)
    return ctx, new_plan, replans_used


async def do_replan(  # noqa: PLR0913
    config: HybridLoopConfig,
    ctx: AgentContext,
    provider: CompletionProvider,
    planner_model: str,
    completion_config: CompletionConfig,
    current_plan: ExecutionPlan,
    trigger_step: PlanStep,
    turns: list[TurnRecord],
    *,
    step_failed: bool = True,
    checkpoint_callback: CheckpointCallback | None = None,
) -> tuple[AgentContext, ExecutionPlan] | ExecutionResult:
    """Generate a revised plan after a step failure or replan trigger.

    Args:
        config: Hybrid loop configuration.
        ctx: Agent context.
        provider: LLM completion provider.
        planner_model: Model ID for the planner.
        completion_config: Completion configuration.
        current_plan: The current execution plan.
        trigger_step: The step that triggered replanning.
        turns: Mutable list of turn records.
        step_failed: Whether the trigger step failed.
        checkpoint_callback: Optional checkpoint callback to thread
            to the planner call.

    Returns:
        ``(ctx, new_plan)`` on success, or ``ExecutionResult``
        for termination conditions.
    """
    logger.info(
        EXECUTION_PLAN_REPLAN_START,
        execution_id=ctx.execution_id,
        trigger_step=trigger_step.step_number,
        step_failed=step_failed,
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

    if step_failed:
        trigger_line = (
            f"Step {trigger_step.step_number} failed: {trigger_step.description}"
        )
    else:
        trigger_line = (
            f"Step {trigger_step.step_number} completed "
            f"successfully, but the remaining plan needs "
            f"adjustment based on what was learned"
        )

    replan_content = (
        f"{trigger_line}\n\n"
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
    result = await call_planner(
        ctx,
        provider,
        planner_model,
        completion_config,
        turns,
        replan_msg,
        revision_number=current_plan.revision_number + 1,
        checkpoint_callback=checkpoint_callback,
    )
    if isinstance(result, ExecutionResult):
        return result
    ctx, plan = result
    plan = truncate_plan(plan, config.max_plan_steps, ctx.execution_id)
    logger.info(
        EXECUTION_PLAN_REPLAN_COMPLETE,
        execution_id=ctx.execution_id,
        step_count=len(plan.steps),
        revision=plan.revision_number,
    )
    return ctx, plan
