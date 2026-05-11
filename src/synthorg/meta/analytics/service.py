"""AnalyticsService -- read-only view over :class:`SignalsService`.

Per the META-MCP-2 plan's analytics-scope decision (D3), this facade
is a thin projection on top of the org signal snapshot; it does not
own its own aggregation pipeline or caching layer.  That keeps the
numeric truth in exactly one place (SignalsService) and removes any
drift risk between ``synthorg_signals_*`` and ``synthorg_analytics_*``
responses over the same window.

Forecast and trend detection are stateless functions on top of the
snapshot fields; if profiling later shows repeated snapshot cost, a
caching strategy plugs in behind :class:`SignalsService` rather than
growing this module.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.analytics.models import (
    AnalyticsForecast,
    AnalyticsOverview,
    AnalyticsTrends,
    MetricsHistory,
    MetricsHistoryPoint,
    MetricsSnapshot,
    MetricTrend,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_SIGNAL_AGGREGATION_COMPLETED

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.meta.signal_models import (
        OrgBudgetSummary,
        OrgPerformanceSummary,
    )
    from synthorg.meta.signals.service import SignalsService

logger = get_logger(__name__)

_MAX_HISTORY_SAMPLE_COUNT: Final[int] = 128


class AnalyticsService:
    """Read-only analytics view built on top of :class:`SignalsService`.

    Args:
        signals: The signals facade holding the authoritative snapshot
            pipeline.
    """

    def __init__(self, *, signals: SignalsService) -> None:
        self._signals = signals

    async def get_overview(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> AnalyticsOverview:
        """Produce an org overview for the window."""
        snapshot = await self._signals.get_org_snapshot(since=since, until=until)
        return AnalyticsOverview(
            avg_quality_score=snapshot.performance.avg_quality_score,
            avg_success_rate=snapshot.performance.avg_success_rate,
            total_spend=snapshot.budget.total_spend,
            days_until_budget_exhausted=snapshot.budget.days_until_exhausted,
            total_error_findings=snapshot.errors.total_findings,
            total_proposals=snapshot.evolution.total_proposals,
        )

    async def get_trends(
        self,
        *,
        since: datetime,
        until: datetime,
        metric_names: Sequence[str] | None = None,
    ) -> AnalyticsTrends:
        """Return per-metric trends for the window.

        The underlying ``OrgPerformanceSummary`` already carries trend
        directions per metric; this method filters / projects them.
        """
        if since >= until:
            msg = "since must be earlier than until"
            raise ValueError(msg)
        perf: OrgPerformanceSummary = await self._signals.get_performance(
            since=since,
            until=until,
        )
        window_days = max(int((until - since).total_seconds() // 86400), 1)
        chosen_names = set(metric_names) if metric_names else None
        trends: list[MetricTrend] = []
        for metric in perf.metrics:
            if chosen_names is not None and metric.name not in chosen_names:
                continue
            trends.append(
                MetricTrend(
                    name=NotBlankStr(metric.name),
                    current_value=metric.value,
                    direction=metric.trend,
                    window_days=metric.window_days,
                ),
            )
        return AnalyticsTrends(
            metrics=tuple(trends),
            window_days=window_days,
        )

    async def get_forecast(
        self,
        *,
        since: datetime,
        until: datetime,
        horizon_days: int = 30,
    ) -> AnalyticsForecast:
        """Return a simple linear forecast derived from the budget window."""
        if horizon_days < 1:
            msg = f"horizon_days must be >= 1, got {horizon_days}"
            raise ValueError(msg)
        if since >= until:
            msg = "since must be earlier than until"
            raise ValueError(msg)
        budget: OrgBudgetSummary = await self._signals.get_budget(
            since=since,
            until=until,
        )
        window_hours = (until - since).total_seconds() / 3600
        daily_rate = budget.total_spend / window_hours * 24.0
        return AnalyticsForecast(
            horizon_days=horizon_days,
            days_until_budget_exhausted=budget.days_until_exhausted,
            confidence=budget.forecast_confidence,
            projected_spend=max(daily_rate * horizon_days, 0.0),
        )

    async def get_current_metrics(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        metric_names: Sequence[str] | None = None,
    ) -> MetricsSnapshot:
        """Return the flat current-value metrics map for the window."""
        snapshot = await self._signals.get_org_snapshot(since=since, until=until)
        raw: dict[str, float] = {
            "performance.avg_quality_score": snapshot.performance.avg_quality_score,
            "performance.avg_success_rate": snapshot.performance.avg_success_rate,
            "performance.avg_collaboration_score": (
                snapshot.performance.avg_collaboration_score
            ),
            "performance.agent_count": float(snapshot.performance.agent_count),
            "budget.total_spend": snapshot.budget.total_spend,
            "budget.productive_ratio": snapshot.budget.productive_ratio,
            "budget.coordination_ratio": snapshot.budget.coordination_ratio,
            "budget.system_ratio": snapshot.budget.system_ratio,
            "errors.total_findings": float(snapshot.errors.total_findings),
            "evolution.total_proposals": float(snapshot.evolution.total_proposals),
            "evolution.approval_rate": snapshot.evolution.approval_rate,
            "telemetry.event_count": float(snapshot.telemetry.event_count),
            "telemetry.error_event_count": float(
                snapshot.telemetry.error_event_count,
            ),
        }
        if metric_names is None:
            metrics = raw
        else:
            wanted = set(metric_names)
            metrics = {k: v for k, v in raw.items() if k in wanted}
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="analytics.metrics",
            metric_count=len(metrics),
        )
        return MetricsSnapshot(metrics=metrics)

    async def get_metric_history(
        self,
        *,
        since: datetime,
        until: datetime,
        metric_names: Sequence[str],
        sample_count: int = 8,
    ) -> MetricsHistory:
        """Return evenly-spaced point-in-time samples across the window.

        Each sample calls :meth:`get_current_metrics` against a sub-
        window.  For a thin read facade this keeps the history shape
        consistent with the current snapshot (no separate sampling
        pipeline) -- acceptable for small ``sample_count`` values.
        Callers that need finer-grained history belong in a durable
        metrics store that can back a future implementation.
        """
        if sample_count < 1:
            msg = f"sample_count must be >= 1, got {sample_count}"
            raise ValueError(msg)
        if sample_count > _MAX_HISTORY_SAMPLE_COUNT:
            msg = (
                f"sample_count must be <= {_MAX_HISTORY_SAMPLE_COUNT}, "
                f"got {sample_count}"
            )
            raise ValueError(msg)
        if since >= until:
            msg = "since must be earlier than until"
            raise ValueError(msg)
        step = (until - since) / sample_count
        sample_windows = tuple(
            (since + step * i, since + step * (i + 1)) for i in range(sample_count)
        )

        async def sample(
            window_start: datetime,
            window_end: datetime,
        ) -> MetricsSnapshot:
            return await self.get_current_metrics(
                since=window_start,
                until=window_end,
                metric_names=metric_names,
            )

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(sample(w[0], w[1])) for w in sample_windows]

        points = tuple(
            MetricsHistoryPoint(
                timestamp=w[1].astimezone(UTC),
                values=task.result().metrics,
            )
            for w, task in zip(sample_windows, tasks, strict=True)
        )
        return MetricsHistory(
            metric_names=tuple(NotBlankStr(n) for n in metric_names),
            points=points,
        )


__all__ = [
    "AnalyticsService",
]
