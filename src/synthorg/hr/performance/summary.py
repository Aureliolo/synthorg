"""Pure functions for extracting API-friendly performance summaries.

Transforms :class:`AgentPerformanceSnapshot` into a flat
:class:`AgentPerformanceSummary` suitable for dashboard display.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import TrendResult, WindowMetrics  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.hr.performance.models import AgentPerformanceSnapshot

_WINDOW_7D = "7d"
_WINDOW_30D = "30d"
_SUCCESS_RATE_METRIC = "success_rate"


class AgentPerformanceSummary(BaseModel):
    """Flat performance summary for dashboard display.

    Derived from :class:`~synthorg.hr.performance.models.AgentPerformanceSnapshot`
    via :func:`extract_performance_summary`.

    Attributes:
        agent_name: Agent display name.
        tasks_completed_total: Best available completed task count
            (max across all time windows).
        tasks_completed_7d: Tasks completed in the last 7 days.
        tasks_completed_30d: Tasks completed in the last 30 days.
        avg_completion_time_seconds: Average task duration
            (30d window, falling back to 7d).
        success_rate_percent: Task success rate as percentage
            (30d window, falling back to 7d).
        cost_per_task: Average cost per task in the configured display currency
            (30d window, falling back to 7d).
        quality_score: Overall quality score (0.0-10.0).
        collaboration_score: Overall collaboration score (0.0-10.0).
        trend_direction: Primary trend direction.
        windows: Rolling window metrics from snapshot.
        trends: Trend results from snapshot.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_name: NotBlankStr = Field(description="Agent display name")
    tasks_completed_total: int = Field(
        ge=0,
        description="Best available completed task count (max across all time windows)",
    )
    tasks_completed_7d: int = Field(
        ge=0,
        description="Tasks completed in the last 7 days",
    )
    tasks_completed_30d: int = Field(
        ge=0,
        description="Tasks completed in the last 30 days",
    )
    avg_completion_time_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Average task duration in seconds (30d window, falling back to 7d)",
    )
    success_rate_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Task success rate as percentage (30d window, falling back to 7d)",
    )
    cost_per_task: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Average cost per task in the configured display currency"
            " (30d window, falling back to 7d)"
        ),
    )
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Overall quality score",
    )
    collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Overall collaboration score",
    )
    trend_direction: TrendDirection = Field(description="Primary trend direction")
    windows: tuple[WindowMetrics, ...] = Field(
        default=(),
        description="Rolling window metrics",
    )
    trends: tuple[TrendResult, ...] = Field(
        default=(),
        description="Trend detection results",
    )


def _primary_trend_direction(
    snapshot: AgentPerformanceSnapshot,
) -> TrendDirection:
    """Pick the primary trend direction from the snapshot.

    Prefers the ``success_rate`` trend.  Falls back to the first
    trend result, then ``INSUFFICIENT_DATA`` if no trends exist.
    """
    for trend in snapshot.trends:
        if trend.metric_name == _SUCCESS_RATE_METRIC:
            return trend.direction
    if snapshot.trends:
        return snapshot.trends[0].direction
    return TrendDirection.INSUFFICIENT_DATA


def _success_rate_to_percent(rate: float | None) -> float | None:
    """Convert a 0.0-1.0 success rate to a 0.0-100.0 percentage."""
    if rate is None:
        return None
    return round(rate * 100.0, 2)


def extract_performance_summary(
    snapshot: AgentPerformanceSnapshot,
    agent_name: NotBlankStr,
) -> AgentPerformanceSummary:
    """Flatten an ``AgentPerformanceSnapshot`` into an API-friendly summary.

    Args:
        snapshot: Full performance snapshot from
            :meth:`PerformanceTracker.get_snapshot`.
        agent_name: Agent display name for the response.

    Returns:
        A flat summary suitable for dashboard rendering.
    """
    window_map = {w.window_size: w for w in snapshot.windows}
    w7 = window_map.get(_WINDOW_7D)
    w30 = window_map.get(_WINDOW_30D)

    # Best available completed count = max tasks_completed across all
    # windows (windows overlap, so sum() would double-count; max()
    # gives the widest window's count as the best approximation).
    tasks_total = max(
        (w.tasks_completed for w in snapshot.windows),
        default=0,
    )

    # Prefer the 30d window for rate/average metrics, fall back to 7d.
    primary = w30 or w7

    return AgentPerformanceSummary(
        agent_name=agent_name,
        tasks_completed_total=tasks_total,
        tasks_completed_7d=w7.tasks_completed if w7 else 0,
        tasks_completed_30d=w30.tasks_completed if w30 else 0,
        avg_completion_time_seconds=(
            primary.avg_completion_time_seconds if primary else None
        ),
        success_rate_percent=_success_rate_to_percent(
            primary.success_rate if primary else None,
        ),
        cost_per_task=primary.avg_cost_per_task if primary else None,
        quality_score=snapshot.overall_quality_score,
        collaboration_score=snapshot.overall_collaboration_score,
        trend_direction=_primary_trend_direction(snapshot),
        windows=snapshot.windows,
        trends=snapshot.trends,
    )
