"""Multi-dimensional velocity calculator -- points per sprint with secondaries.

Provides a comprehensive velocity view: primary throughput (pts/sprint)
plus per-task, per-day, and completion ratio secondary metrics.
Designed for hybrid strategies that blend calendar and task-driven
scheduling.
"""

from collections.abc import Sequence

from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
    VelocityMetrics,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    VELOCITY_MULTI_NO_DURATION,
    VELOCITY_MULTI_NO_TASK_COUNT,
)

logger = get_logger(__name__)

_UNIT: str = "pts/sprint"


class MultiDimensionalVelocityCalculator:
    """Velocity calculator with multiple dimensions.

    Primary unit: ``pts/sprint`` (raw throughput, no normalization).

    Secondary metrics:

    - ``pts_per_task``: points per task completed (0.0 when unavailable).
    - ``pts_per_day``: points per calendar day.
    - ``completion_ratio``: ratio of completed to committed points.
    """

    __slots__ = ()

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute multi-dimensional velocity from a single record.

        Args:
            record: A completed sprint's velocity record.

        Returns:
            Velocity metrics with ``pts/sprint`` as primary unit and
            ``pts_per_task``, ``pts_per_day``, ``completion_ratio``
            as secondary metrics.
        """
        pts_sprint = record.story_points_completed
        if record.duration_days == 0:
            logger.debug(
                VELOCITY_MULTI_NO_DURATION,
                sprint_id=record.sprint_id,
            )
            pts_per_day = 0.0
        else:
            pts_per_day = pts_sprint / record.duration_days

        task_count = record.task_completion_count
        if task_count is None:
            logger.debug(
                VELOCITY_MULTI_NO_TASK_COUNT,
                sprint_id=record.sprint_id,
            )
        pts_per_task = 0.0 if not task_count else pts_sprint / task_count

        return VelocityMetrics(
            primary_value=pts_sprint,
            primary_unit=_UNIT,
            secondary={
                "pts_per_task": pts_per_task,
                "pts_per_day": pts_per_day,
                "completion_ratio": record.completion_ratio,
            },
        )

    def rolling_average(
        self,
        records: Sequence[VelocityRecord],
        window: int,
    ) -> VelocityMetrics:
        """Compute rolling average with all secondary dimensions.

        Uses the last *window* records.  ``pts_per_day`` is weighted
        by duration_days.  ``pts_per_task`` uses only records with
        a valid ``task_completion_count``.  ``completion_ratio`` is
        an unweighted arithmetic mean across the window.

        Args:
            records: Ordered velocity records (oldest first).
            window: Number of recent sprints to average over.

        Returns:
            Averaged velocity metrics across all dimensions.
        """
        if not records or window < 1:
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
            )
        recent = records[-window:]
        n = len(recent)
        total_pts = sum(r.story_points_completed for r in recent)
        total_days = sum(r.duration_days for r in recent)

        return VelocityMetrics(
            primary_value=total_pts / n,
            primary_unit=_UNIT,
            secondary={
                "pts_per_task": _weighted_pts_per_task(recent),
                "pts_per_day": (total_pts / total_days if total_days > 0 else 0.0),
                "completion_ratio": (sum(r.completion_ratio for r in recent) / n),
                "sprints_averaged": float(n),
            },
        )

    @property
    def calculator_type(self) -> VelocityCalcType:
        """Return MULTI_DIMENSIONAL."""
        return VelocityCalcType.MULTI_DIMENSIONAL

    @property
    def primary_unit(self) -> str:
        """Return ``pts/sprint``."""
        return _UNIT


def _weighted_pts_per_task(
    records: Sequence[VelocityRecord],
) -> float:
    """Compute weighted pts/task, skipping records without counts.

    Returns:
        The aggregate story points divided by aggregate task count
        across records that report a positive count; ``0.0`` when
        no records carry a usable count.
    """
    task_pts = 0.0
    task_total = 0
    for r in records:
        count = r.task_completion_count
        if count is not None and count > 0:
            task_pts += r.story_points_completed
            task_total += count
    return task_pts / task_total if task_total > 0 else 0.0
