"""Progress-summary and replanning helpers for the Hybrid loop.

Owns the "completed step -> summarise + decide whether to replan ->
re-plan" half of the hybrid loop. Calls back into step-helpers for the
shared planner-call body.
"""

import json
import re
from collections.abc import Callable

from synthorg.core.normalization import compare_ci
from synthorg.engine.hybrid.step_helpers import (
    call_planner,
    truncate_plan,
)
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_control_helpers import (
    check_budget,
    check_shutdown,
)
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
    invoke_checkpoint_callback,
    update_step_status,
)
from synthorg.engine.plan_loop_context import (
    ReplanTrigger,
    ReplanVerdict,
    StepRunContext,
    StepRunState,
)
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
        # The body is a raw model completion that can echo task and tool
        # content, so record its shape rather than any of its text.
        logger.warning(
            EXECUTION_HYBRID_REPLAN_PARSE_TRACE,
            parser="fallback",
            note="replan key found but value not parsed as true; "
            "defaulting to no replan",
            content_length=len(content),
        )
    return False


async def run_progress_summary(
    config: HybridLoopConfig,
    run: StepRunContext,
    state: StepRunState,
) -> ReplanVerdict | ExecutionResult:
    """Produce a progress summary and determine if replanning is needed.

    Advances ``state.ctx`` across the summary turn and appends the turn
    record to ``state.turns``.

    Args:
        config: Hybrid loop configuration.
        run: Run-scoped collaborators.
        state: Mutable loop cursor; ``step_idx`` names the completed step.

    Returns:
        The :class:`ReplanVerdict` the LLM asked for, or an
        :class:`ExecutionResult` for termination conditions.
    """
    if not state.ctx.has_turns_remaining:
        return build_result(state.ctx, TerminationReason.MAX_TURNS, state.turns)

    shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
    if shutdown_result is not None:
        return shutdown_result
    budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
    if budget_result is not None:
        return budget_result

    step_idx = state.step_idx
    state.ctx = state.ctx.with_message(_summary_message(config, state))
    turn_number = state.ctx.turn_count + 1

    response = await call_provider(
        state.ctx,
        run.provider,
        run.planner_model,
        tool_defs=None,
        config=run.completion_config,
        turn_number=turn_number,
        turns=state.turns,
    )
    if isinstance(response, ExecutionResult):
        return response

    state.turns.append(
        make_turn_record(
            turn_number,
            response,
            call_category=classify_turn(
                turn_number,
                response,
                state.ctx,
                is_planning_phase=True,
            ),
            provider_metadata=response.provider_metadata,
        )
    )

    error = check_response_errors(state.ctx, response, turn_number, state.turns)
    if error is not None:
        return error

    state.ctx = state.ctx.with_turn_completed(
        response.usage,
        response_to_message(response),
    )
    logger.info(
        EXECUTION_HYBRID_PROGRESS_SUMMARY,
        execution_id=state.ctx.execution_id,
        turn=turn_number,
        step_completed=step_idx + 1,
    )

    await invoke_checkpoint_callback(run.checkpoint_callback, state.ctx, turn_number)

    raw_content = response.content or ""
    if not raw_content.strip():
        logger.warning(
            EXECUTION_HYBRID_PROGRESS_SUMMARY_EMPTY,
            execution_id=state.ctx.execution_id,
            note="empty progress summary response",
        )
    return ReplanVerdict.from_flag(replan=_parse_replan_decision(raw_content))


def _summary_message(
    config: HybridLoopConfig,
    state: StepRunState,
) -> ChatMessage:
    """Build the progress-summary prompt for the step just completed.

    Returns:
        The user message asking for a summary, and for a replan decision
        when a replan could still act on one.
    """
    plan = state.plan
    step_idx = state.step_idx
    return ChatMessage(
        role=MessageRole.USER,
        content=_build_summary_prompt(
            plan,
            step_idx,
            ask_replan=(
                config.allow_replan_on_completion and step_idx < len(plan.steps) - 1
            ),
        ),
    )


async def attempt_replan(
    config: HybridLoopConfig,
    run: StepRunContext,
    state: StepRunState,
    step: PlanStep,
    *,
    finalize: _Finalize,
) -> ExecutionResult | None:
    """Handle a failed step: mark it, check replan budget, replan.

    On success the revised plan is adopted onto ``state`` (plan rebound,
    replan counter incremented, plan appended to the history).

    Args:
        config: Hybrid loop configuration.
        run: Run-scoped collaborators.
        state: Mutable loop cursor; ``step_idx`` names the failed step.
        step: The failed step.
        finalize: Callable that attaches hybrid metadata to a result.

    Returns:
        ``None`` once the revised plan is adopted, or an
        :class:`ExecutionResult` for termination conditions.
    """
    state.plan = update_step_status(state.plan, state.step_idx, StepStatus.FAILED)
    logger.warning(
        EXECUTION_PLAN_STEP_FAILED,
        execution_id=state.ctx.execution_id,
        step_number=step.step_number,
    )

    if state.replans_used >= config.max_replans:
        logger.error(
            EXECUTION_PLAN_REPLAN_EXHAUSTED,
            execution_id=state.ctx.execution_id,
            replans_used=state.replans_used,
            max_replans=config.max_replans,
        )
        error_msg = (
            f"Max replans ({config.max_replans}) exhausted "
            f"after step {step.step_number} failed"
        )
        return finalize(
            build_result(
                state.ctx,
                TerminationReason.ERROR,
                state.turns,
                error_message=error_msg,
            ),
            state.all_plans,
            state.replans_used,
        )

    if not state.ctx.has_turns_remaining:
        return finalize(
            build_result(state.ctx, TerminationReason.MAX_TURNS, state.turns),
            state.all_plans,
            state.replans_used,
        )

    shutdown_result = check_shutdown(state.ctx, run.shutdown_checker, state.turns)
    if shutdown_result is not None:
        return finalize(shutdown_result, state.all_plans, state.replans_used)
    budget_result = check_budget(state.ctx, run.budget_checker, state.turns)
    if budget_result is not None:
        return finalize(budget_result, state.all_plans, state.replans_used)

    replan_result = await do_replan(
        config, run, state, step, trigger=ReplanTrigger.STEP_FAILURE
    )
    if isinstance(replan_result, ExecutionResult):
        return finalize(replan_result, state.all_plans, state.replans_used)

    state.record_replan(replan_result)
    return None


async def do_replan(
    config: HybridLoopConfig,
    run: StepRunContext,
    state: StepRunState,
    trigger_step: PlanStep,
    *,
    trigger: ReplanTrigger,
) -> ExecutionPlan | ExecutionResult:
    """Generate a revised plan after a step failure or replan trigger.

    Advances ``state.ctx`` across the planner turn but leaves adopting the
    returned plan to the caller: the three call sites differ in whether the
    replan counts against ``max_replans`` and whether a pending steering
    directive is cleared.

    Args:
        config: Hybrid loop configuration.
        run: Run-scoped collaborators.
        state: Mutable loop cursor supplying the current plan and turns.
        trigger_step: The step that triggered replanning.
        trigger: What prompted the replan, which selects the prompt wording
            and is recorded on the replan-start event.

    Returns:
        The revised :class:`ExecutionPlan` on success, or an
        :class:`ExecutionResult` for termination conditions.
    """
    current_plan = state.plan
    logger.info(
        EXECUTION_PLAN_REPLAN_START,
        execution_id=state.ctx.execution_id,
        trigger=trigger.value,
        step_number=trigger_step.step_number,
        directive_id=state.ctx.pending_steering_replan_id,
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

    if trigger.step_failed:
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
        run,
        state.ctx,
        state.turns,
        replan_msg,
        revision_number=current_plan.revision_number + 1,
    )
    if isinstance(result, ExecutionResult):
        return result
    state.ctx, plan = result
    plan = truncate_plan(plan, config.max_plan_steps)
    logger.info(
        EXECUTION_PLAN_REPLAN_COMPLETE,
        execution_id=state.ctx.execution_id,
        step_count=len(plan.steps),
        revision=plan.revision_number,
    )
    return plan
