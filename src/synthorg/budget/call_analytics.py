"""Per-call analytics aggregation and alerting service."""

from collections import Counter
from typing import TYPE_CHECKING, Final

from synthorg.budget.call_analytics_models import AnalyticsAggregation
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import (
    ANALYTICS_AGGREGATION_COMPUTED,
    ANALYTICS_RETRY_RATE_ALERT,
    ANALYTICS_SERVICE_CREATED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.budget.call_analytics_config import CallAnalyticsConfig
    from synthorg.budget.category_analytics import OrchestrationRatio
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.types import NotBlankStr
    from synthorg.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)

# A call is considered "retried" once it has at least one retry attempt;
# zero-retry calls are excluded from the retry-rate numerator.
_MIN_RETRY_COUNT: Final[int] = 1

# 95th-percentile interpolation factor (NIST type-7 / linear-interpolation
# definition): pick the value at index 0.95 * (n - 1).
_PERCENTILE_INTERPOLATION_FACTOR: Final[float] = 0.95


class CallAnalyticsService:
    """Aggregates per-call metrics and dispatches threshold alerts.

    Attributes are read-only after construction.  All public methods
    are coroutines.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTracker,
        config: CallAnalyticsConfig,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        """Create a CallAnalyticsService.

        Args:
            cost_tracker: Source of cost records.
            config: Analytics configuration.
            notification_dispatcher: Optional dispatcher for alerts.
        """
        self._tracker = cost_tracker
        self._config = config
        self._dispatcher = notification_dispatcher
        logger.debug(ANALYTICS_SERVICE_CREATED, enabled=config.enabled)

    async def get_aggregation(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AnalyticsAggregation:
        """Compute aggregated analytics over cost records.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            start: Inclusive lower bound on timestamp.
            end: Exclusive upper bound on timestamp.

        Returns:
            Aggregated analytics over the matching records.
        """
        records = await self._tracker.get_records(
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            start=start,
            end=end,
        )
        orchestration_ratio = await self._tracker.get_orchestration_ratio(
            agent_id=agent_id,
            task_id=task_id,
            start=start,
            end=end,
            thresholds=self._config.orchestration_alerts,
        )
        agg = _build_aggregation(records, orchestration_ratio)
        logger.debug(
            ANALYTICS_AGGREGATION_COMPUTED,
            total_calls=agg.total_calls,
            retry_rate=agg.retry_rate,
        )
        return agg

    async def check_alerts(
        self,
        records: tuple[CostRecord, ...],
    ) -> None:
        """Check alert thresholds and dispatch notifications if crossed.

        Args:
            records: Cost records to evaluate.
        """
        if not self._config.enabled or not records:
            return

        total = len(records)
        retried = sum(
            1
            for r in records
            if r.retry_count is not None and r.retry_count >= _MIN_RETRY_COUNT
        )
        retry_rate = retried / total

        if retry_rate > self._config.retry_alerts.warn_rate:
            logger.warning(
                ANALYTICS_RETRY_RATE_ALERT,
                retry_rate=retry_rate,
                warn_rate=self._config.retry_alerts.warn_rate,
            )
            if self._dispatcher is not None:
                await _dispatch_retry_rate_alert(
                    self._dispatcher,
                    retry_rate=retry_rate,
                    warn_rate=self._config.retry_alerts.warn_rate,
                )


# ── Pure helpers ─────────────────────────────────────────────────────────────


def _build_aggregation(
    records: tuple[CostRecord, ...],
    orchestration_ratio: OrchestrationRatio,
) -> AnalyticsAggregation:
    """Build an AnalyticsAggregation from records.

    Args:
        records: Cost records to aggregate.
        orchestration_ratio: Pre-computed orchestration ratio.

    Returns:
        Populated AnalyticsAggregation.
    """
    total = len(records)

    success_count = sum(1 for r in records if r.success is True)
    failure_count = sum(1 for r in records if r.success is False)

    retried = sum(
        1 for r in records if r.retry_count is not None and r.retry_count >= 1
    )
    retry_rate = retried / total if total > 0 else 0.0

    cache_reporting = [r for r in records if r.cache_hit is not None]
    cache_hit_count = sum(1 for r in cache_reporting if r.cache_hit is True)
    cache_hit_rate = cache_hit_count / len(cache_reporting) if cache_reporting else None

    latencies = [r.latency_ms for r in records if r.latency_ms is not None]
    avg_latency_ms = sum(latencies) / len(latencies) if latencies else None
    p95_latency_ms = _p95(latencies) if latencies else None

    reason_counts: Counter[str] = Counter(
        r.finish_reason.value for r in records if r.finish_reason is not None
    )
    by_finish_reason = tuple(sorted(reason_counts.items()))

    return AnalyticsAggregation(
        total_calls=total,
        success_count=success_count,
        failure_count=failure_count,
        retry_count=retried,
        retry_rate=retry_rate,
        cache_hit_count=cache_hit_count,
        cache_hit_rate=cache_hit_rate,
        avg_latency_ms=avg_latency_ms,
        p95_latency_ms=p95_latency_ms,
        orchestration_ratio=orchestration_ratio,
        by_finish_reason=by_finish_reason,
    )


def _p95(values: list[float]) -> float:
    """Compute the 95th percentile via linear interpolation.

    Args:
        values: List of values (at least one element).

    Returns:
        95th-percentile value.
    """
    values = sorted(values)
    n = len(values)
    if n == 1:
        return values[0]
    index = _PERCENTILE_INTERPOLATION_FACTOR * (n - 1)
    lo = int(index)
    hi = lo + 1
    frac = index - lo
    if hi >= n:
        return values[-1]
    return values[lo] + frac * (values[hi] - values[lo])


async def _dispatch_retry_rate_alert(
    dispatcher: NotificationDispatcher,
    *,
    retry_rate: float,
    warn_rate: float,
) -> None:
    """Dispatch a retry-rate warning notification.

    Args:
        dispatcher: Notification dispatcher.
        retry_rate: Observed retry rate.
        warn_rate: Configured warn threshold.
    """
    from synthorg.notifications.models import (  # noqa: PLC0415
        Notification,
        NotificationCategory,
        NotificationSeverity,
    )

    body = f"Retry rate {retry_rate:.1%} exceeds warning threshold {warn_rate:.1%}."
    try:
        await dispatcher.dispatch(
            Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.WARNING,
                title="High retry rate alert",
                body=body,
                source="budget.call_analytics",
            ),
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            ANALYTICS_RETRY_RATE_ALERT,
            error="retry_rate_alert_dispatch_failed",
            exc_info=True,
        )
