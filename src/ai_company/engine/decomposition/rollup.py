"""Subtask status rollup computation.

Pure function for aggregating subtask statuses into a parent status.
"""

from ai_company.core.enums import TaskStatus
from ai_company.engine.decomposition.models import SubtaskStatusRollup
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_ROLLUP_COMPUTED,
)

logger = get_logger(__name__)


class StatusRollup:
    """Computes aggregated status rollup from subtask statuses."""

    @staticmethod
    def compute(
        parent_task_id: str,
        subtask_statuses: tuple[TaskStatus, ...],
    ) -> SubtaskStatusRollup:
        """Compute a status rollup from subtask statuses.

        Rules:
        - All COMPLETED -> COMPLETED
        - All CANCELLED -> CANCELLED
        - Any FAILED (and not all terminal) -> FAILED
        - Any IN_PROGRESS -> IN_PROGRESS
        - Any BLOCKED (none IN_PROGRESS) -> BLOCKED
        - Otherwise -> IN_PROGRESS (work still pending)

        Args:
            parent_task_id: The parent task identifier.
            subtask_statuses: Statuses of all subtasks.

        Returns:
            Aggregated status rollup.
        """
        total = len(subtask_statuses)
        completed = subtask_statuses.count(TaskStatus.COMPLETED)
        failed = subtask_statuses.count(TaskStatus.FAILED)
        in_progress = subtask_statuses.count(TaskStatus.IN_PROGRESS)
        blocked = subtask_statuses.count(TaskStatus.BLOCKED)
        cancelled = subtask_statuses.count(TaskStatus.CANCELLED)

        rollup = SubtaskStatusRollup(
            parent_task_id=parent_task_id,
            total=total,
            completed=completed,
            failed=failed,
            in_progress=in_progress,
            blocked=blocked,
            cancelled=cancelled,
        )

        logger.debug(
            DECOMPOSITION_ROLLUP_COMPUTED,
            parent_task_id=parent_task_id,
            total=total,
            derived_status=rollup.derived_parent_status.value,
        )

        return rollup
