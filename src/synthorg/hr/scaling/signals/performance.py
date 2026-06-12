"""Performance signal source -- reads trend data from tracker."""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_SIGNAL_COLLECTION_DEGRADED

logger = get_logger(__name__)

_SOURCE_NAME = NotBlankStr("performance")

# Map trend direction strings to numeric values for signal thresholds.
_TREND_MAP: dict[str, float] = {
    "improving": 1.0,
    "stable": 0.0,
    "declining": -1.0,
    "insufficient_data": 0.0,
}


class PerformanceSignalSource:
    """Read-only adapter over the performance tracker.

    Converts ``AgentPerformanceSnapshot`` trend data into
    aggregate scaling signals.
    """

    @property
    def name(self) -> NotBlankStr:
        """Source identifier."""
        return _SOURCE_NAME

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],
        *,
        snapshots: dict[NotBlankStr, AgentPerformanceSnapshot] | None = None,
    ) -> tuple[ScalingSignal, ...]:
        """Collect performance trend signals.

        Args:
            agent_ids: Active agent IDs.
            snapshots: Performance snapshots keyed by agent_id.

        Returns:
            Performance signals: avg_quality_trend,
            declining_agent_count.
        """
        now = datetime.now(UTC)

        if not snapshots:
            if agent_ids:
                logger.warning(
                    HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                    source="performance",
                    reason="no_snapshots_for_active_agents",
                    agent_count=len(agent_ids),
                )
            return (
                ScalingSignal(
                    name=NotBlankStr("avg_quality_trend"),
                    value=0.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
                ScalingSignal(
                    name=NotBlankStr("declining_agent_count"),
                    value=0.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
            )

        trend_values: list[float] = []
        declining_count = 0

        for agent_id in agent_ids:
            snapshot = snapshots.get(agent_id)
            if snapshot is None:
                trend_values.append(0.0)
                continue
            quality_trend = next(
                (t for t in snapshot.trends if t.metric_name == "quality_score"),
                None,
            )
            if quality_trend is not None:
                direction = str(quality_trend.direction)
                if direction not in _TREND_MAP:
                    logger.warning(
                        HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                        source="performance",
                        reason="unknown_trend_direction",
                        direction=direction,
                    )
                value = _TREND_MAP.get(direction, 0.0)
                trend_values.append(value)
                if direction == "declining":
                    declining_count += 1
            else:
                trend_values.append(0.0)

        avg_trend = sum(trend_values) / len(trend_values) if trend_values else 0.0

        return (
            ScalingSignal(
                name=NotBlankStr("avg_quality_trend"),
                value=round(avg_trend, 4),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=NotBlankStr("declining_agent_count"),
                value=float(declining_count),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
        )
