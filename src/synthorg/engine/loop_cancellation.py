"""Per-task cancellation safe-boundary check for the execution loops.

Separate from ``loop_helpers`` (which is at its size budget) and from the
steering hook (cancellation is general: it also serves the cockpit ``kill``
intervention, not just steering supersession). Consulted at the top-of-turn
safe boundary so a task cancelled externally halts its running agent cleanly
instead of running an obsolete task to completion.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_helpers import build_result
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TaskCancellationChecker,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TASK_CANCELLED,
)

logger = get_logger(__name__)


async def check_task_cancelled(
    ctx: AgentContext,
    cancellation_checker: TaskCancellationChecker | None,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return a CANCELLED result if the task was cancelled/superseded.

    Best-effort: a checker failure is logged and skipped (a transient read
    fault must not fail a healthy run); ``MemoryError`` / ``RecursionError``
    propagate.

    Args:
        ctx: Current agent context.
        cancellation_checker: Optional async callback returning ``True`` when the
            task has reached a terminal/cancelled status; ``None`` disables the
            check.
        turns: Accumulated turn records.

    Returns:
        ``ExecutionResult`` with CANCELLED reason, or ``None`` to continue.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    if cancellation_checker is None:
        return None
    try:
        cancelled = await cancellation_checker()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=ctx.turn_count,
            note="task_cancellation_check_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not cancelled:
        return None
    logger.info(
        EXECUTION_LOOP_TASK_CANCELLED,
        execution_id=ctx.execution_id,
        turn=ctx.turn_count,
    )
    return build_result(ctx, TerminationReason.CANCELLED, turns)
