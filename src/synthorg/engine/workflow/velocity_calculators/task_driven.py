"""Task-driven velocity calculator -- points per task.

Reference implementation for the ``VelocityCalculator`` protocol.
"""

from collections.abc import Sequence

from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
    VelocityMetrics,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    VELOCITY_TASK_DRIVEN_NO_TASK_COUNT,
)

logger = get_logger(__name__)

_UNIT: str = "pts/task"


class TaskDrivenVelocityCalculator:
    """Velocity calculator that measures points per task.

    Primary unit: ``pts/task``.

    When ``task_completion_count`` is not available,
    ``primary_value`` is 0.0 (cannot normalize) but
    ``pts_per_sprint`` is included as a secondary metric.
    """

    __slots__ = ()

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute points-per-task from a single velocity record.

        Args:
            record: A completed sprint's velocity record.

        Returns:
            Velocity metrics with ``pts/task`` as primary unit.
        """
        task_count = record.task_completion_count
        if task_count is None:
            logger.debug(
                VELOCITY_TASK_DRIVEN_NO_TASK_COUNT,
                sprint_id=record.sprint_id,
            )
        if not task_count:
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
                secondary={
                    "pts_per_sprint": record.story_points_completed,
                },
            )
        pts_per_task = record.story_points_completed / task_count
        return VelocityMetrics(
            primary_value=pts_per_task,
            primary_unit=_UNIT,
            secondary={
                "pts_per_sprint": record.story_points_completed,
                "task_count": float(task_count),
            },
        )

    def rolling_average(
        self,
        records: Sequence[VelocityRecord],
        window: int,
    ) -> VelocityMetrics:
        """Compute rolling average of points-per-task.

        Uses the last *window* records.  Records without
        ``task_completion_count`` contribute 0.0 to the average.

        Args:
            records: Ordered velocity records (oldest first).
            window: Number of recent sprints to average over.

        Returns:
            Averaged velocity metrics.
        """
        if not records or window < 1:
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
            )
        recent = records[-window:]
        total_pts = 0.0
        total_tasks = 0
        for r in recent:
            count = r.task_completion_count
            if count is not None and count > 0:
                total_pts += r.story_points_completed
                total_tasks += count
        if total_tasks == 0:
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
            )
        return VelocityMetrics(
            primary_value=total_pts / total_tasks,
            primary_unit=_UNIT,
            secondary={
                "total_tasks": float(total_tasks),
                "sprints_averaged": float(len(recent)),
            },
        )

    @property
    def calculator_type(self) -> VelocityCalcType:
        """Return TASK_DRIVEN."""
        return VelocityCalcType.TASK_DRIVEN

    @property
    def primary_unit(self) -> str:
        """Return ``pts/task``."""
        return _UNIT
