# module-kind: code
"""Reporting the status a run landed on after recovery diagnosed it.

Recovery can move a failed run's task, and the move has to reach the central
engine with a reason an operator can act on. The reason carries the failure
category and, capped, the criteria that went unmet: an operator reading
"post-recovery status: failed" alone learns nothing about what to fix.
"""

from typing import Final

from synthorg.core.task_enums import TaskStatus
from synthorg.engine._task_sync_engine import sync_to_task_engine
from synthorg.engine.recovery import RecoveryResult
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_ENGINE_TASK_TRANSITION

logger = get_logger(__name__)

#: How many unmet criteria the transition reason names before summarising the
#: rest as a count. The reason travels into a status row read in a list, so an
#: uncapped list would push everything else off the line.
_TRANSITION_REASON_CRITERIA_CAP: Final[int] = 5


async def log_post_recovery_transition(
    task_engine: TaskEngine | None,
    recovery_result: RecoveryResult,
    *,
    agent_id: str,
    task_id: str,
    from_status: TaskStatus,
    to_status: TaskStatus,
) -> None:
    """Log the post-recovery task-status transition and sync it.

    Args:
        task_engine: Central engine the move syncs to, or ``None``.
        recovery_result: The diagnosis that moved the task.
        agent_id: The agent that ran it.
        task_id: The task it ran.
        from_status: Status before recovery.
        to_status: Status recovery landed it on.
    """
    logger.info(
        EXECUTION_ENGINE_TASK_TRANSITION,
        agent_id=agent_id,
        task_id=task_id,
        from_status=from_status.value,
        to_status=to_status.value,
    )
    category = recovery_result.failure_category.value
    await sync_to_task_engine(
        task_engine,
        target_status=to_status,
        task_id=task_id,
        agent_id=agent_id,
        reason=(
            f"Post-recovery status: {to_status.value} "
            f"(failure_category={category}{_criteria_suffix(recovery_result)})"
        ),
    )


def _criteria_suffix(recovery_result: RecoveryResult) -> str:
    """Render the unmet criteria for the transition reason.

    Returns:
        A capped, sanitised ``", unmet_criteria=..."`` fragment, or an empty
        string when the diagnosis named none.
    """
    criteria = recovery_result.criteria_failed
    if not criteria:
        return ""
    sanitized = "; ".join(
        sanitize_message(c) for c in criteria[:_TRANSITION_REASON_CRITERIA_CAP]
    )
    overflow = len(criteria) - _TRANSITION_REASON_CRITERIA_CAP
    more = f" +{overflow} more" if overflow > 0 else ""
    return f", unmet_criteria={sanitized}{more}"


__all__ = ["log_post_recovery_transition"]
