"""Points-per-sprint velocity calculator -- simple throughput.

Measures raw story points completed per sprint with no time or task
normalization.  The simplest calculator in the system.
"""

from collections.abc import Sequence

from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
    VelocityMetrics,
)

_UNIT: str = "pts/sprint"


class PointsPerSprintVelocityCalculator:
    """Velocity calculator measuring raw story points per sprint.

    Primary unit: ``pts/sprint``.

    No normalization -- the primary value is simply
    ``story_points_completed``.  Secondary metrics include
    ``completion_ratio`` (and ``sprints_averaged`` in rolling
    averages).
    """

    __slots__ = ()

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute points-per-sprint from a single velocity record.

        Args:
            record: A completed sprint's velocity record.

        Returns:
            Velocity metrics with ``pts/sprint`` as primary unit.
        """
        return VelocityMetrics(
            primary_value=record.story_points_completed,
            primary_unit=_UNIT,
            secondary={
                "completion_ratio": record.completion_ratio,
            },
        )

    def rolling_average(
        self,
        records: Sequence[VelocityRecord],
        window: int,
    ) -> VelocityMetrics:
        """Compute rolling average of points-per-sprint.

        Uses the last *window* records.

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
        total_pts = sum(r.story_points_completed for r in recent)
        total_ratio = sum(r.completion_ratio for r in recent)
        count = len(recent)
        return VelocityMetrics(
            primary_value=total_pts / count,
            primary_unit=_UNIT,
            secondary={
                "completion_ratio": total_ratio / count,
                "sprints_averaged": float(count),
            },
        )

    @property
    def calculator_type(self) -> VelocityCalcType:
        """Return POINTS_PER_SPRINT."""
        return VelocityCalcType.POINTS_PER_SPRINT

    @property
    def primary_unit(self) -> str:
        """Return ``pts/sprint``."""
        return _UNIT
