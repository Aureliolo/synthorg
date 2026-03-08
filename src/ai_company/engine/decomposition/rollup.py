"""Subtask status rollup computation.

Pure function for aggregating subtask statuses into a parent status.
"""

from typing import TYPE_CHECKING

from ai_company.core.enums import TaskStatus
from ai_company.engine.decomposition.models import SubtaskStatusRollup
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_ROLLUP_COMPUTED,
)

if TYPE_CHECKING:
    from ai_company.core.types import NotBlankStr

logger = get_logger(__name__)


class StatusRollup:
    """Computes aggregated status rollup from subtask statuses."""

    @staticmethod
    def compute(
        parent_task_id: NotBlankStr,
        subtask_statuses: tuple[TaskStatus, ...],
    ) -> SubtaskStatusRollup:
        """Compute a status rollup from a collection of subtask statuses.

        Aggregates subtask statuses into a ``SubtaskStatusRollup`` whose
        ``derived_parent_status`` computed field determines the overall
        parent task status based on the aggregated counts.

        Args:
            parent_task_id: The parent task identifier.
            subtask_statuses: Statuses of all subtasks.

        Returns:
            An aggregated status rollup object.
        """
        total = len(subtask_statuses)

        if total == 0:
            logger.warning(
                DECOMPOSITION_ROLLUP_COMPUTED,
                parent_task_id=parent_task_id,
                total=0,
                reason="rollup computed with no subtask statuses",
            )
            return SubtaskStatusRollup(
                parent_task_id=parent_task_id,
                total=0,
                completed=0,
                failed=0,
                in_progress=0,
                blocked=0,
                cancelled=0,
            )

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
