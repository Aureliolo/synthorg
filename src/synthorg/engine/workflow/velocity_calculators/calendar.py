"""Calendar velocity calculator -- points per day.

Measures velocity as story points delivered per calendar day,
weighted by sprint duration for rolling averages.
"""

from collections.abc import Sequence

from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
    VelocityMetrics,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    VELOCITY_CALENDAR_NO_DURATION,
)

logger = get_logger(__name__)

_UNIT: str = "pts/day"


class CalendarVelocityCalculator:
    """Velocity calculator that measures points per calendar day.

    Primary unit: ``pts/day``.

    Rolling averages are weighted by ``duration_days`` so that
    longer sprints contribute proportionally more to the average.
    """

    __slots__ = ()

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute points-per-day from a single velocity record.

        Args:
            record: A completed sprint's velocity record.

        Returns:
            Velocity metrics with ``pts/day`` as primary unit.
        """
        if record.duration_days == 0:
            logger.debug(
                VELOCITY_CALENDAR_NO_DURATION,
                sprint_id=record.sprint_id,
            )
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
                secondary={
                    "pts_per_sprint": record.story_points_completed,
                },
            )
        pts_per_day = record.story_points_completed / record.duration_days
        return VelocityMetrics(
            primary_value=pts_per_day,
            primary_unit=_UNIT,
            secondary={
                "pts_per_sprint": record.story_points_completed,
                "duration_days": float(record.duration_days),
            },
        )

    def rolling_average(
        self,
        records: Sequence[VelocityRecord],
        window: int,
    ) -> VelocityMetrics:
        """Compute duration-weighted rolling average of points-per-day.

        Uses the last *window* records.  The average is weighted by
        ``duration_days`` so that longer sprints contribute more.

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
        total_days = sum(r.duration_days for r in recent)
        if total_days == 0:
            return VelocityMetrics(
                primary_value=0.0,
                primary_unit=_UNIT,
            )
        return VelocityMetrics(
            primary_value=total_pts / total_days,
            primary_unit=_UNIT,
            secondary={
                "total_days": float(total_days),
                "sprints_averaged": float(len(recent)),
            },
        )

    @property
    def calculator_type(self) -> VelocityCalcType:
        """Return CALENDAR."""
        return VelocityCalcType.CALENDAR

    @property
    def primary_unit(self) -> str:
        """Return ``pts/day``."""
        return _UNIT
