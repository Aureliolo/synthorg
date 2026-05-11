"""Shared plan utilities for plan-based execution loops.

Stateless helpers used by both ``PlanExecuteLoop`` and ``HybridLoop``
for common plan-step operations.
"""

from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_PLAN_STEP_INDEX_OUT_OF_RANGE,
    EXECUTION_PLAN_STEP_STATUS_UPDATED,
    EXECUTION_PLAN_SUMMARY_FALLBACK,
)
from synthorg.providers.enums import FinishReason, MessageRole

logger = get_logger(__name__)

_MAX_TASK_SUMMARY_LENGTH: Final[int] = 200
"""Internal constant by design: maximum character length for task
summary strings; defensive truncation prevents bloated summaries.
Not exposed to the settings registry."""

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext
    from synthorg.providers.models import CompletionResponse

    from .plan_models import ExecutionPlan, StepStatus


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
