# module-kind: controller
"""Analytics time-series trends endpoint at /analytics/trends."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg._core.features import require_service
from synthorg.api.controllers.analytics._shared import (
    TrendsResponse,
    _resolve_agent_counts,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.budget.trends import (
    BucketSize,
    TrendDataPoint,
    TrendMetric,
    TrendPeriod,
    bucket_cost_records,
    bucket_success_rate,
    bucket_task_completions,
    generate_bucket_starts,
    period_to_timedelta,
    resolve_bucket_size,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import ANALYTICS_TRENDS_QUERIED
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


def _bucket_task_metric_data(
    task_metrics: Sequence[TaskMetricRecord],
    metric: TrendMetric,
    start: datetime,
    now: datetime,
    bucket_size: BucketSize,
) -> tuple[TrendDataPoint, ...]:
    """Bucket task metric records by the requested metric.

    Args:
        task_metrics: Task metric records.
        metric: TASKS_COMPLETED or SUCCESS_RATE.
        start: Period start.
        now: Period end.
        bucket_size: Bucket granularity.

    Returns:
        Bucketed data points.
    """
    if metric == TrendMetric.TASKS_COMPLETED:
        return bucket_task_completions(
            task_metrics,
            start,
            now,
            bucket_size,
        )
    return bucket_success_rate(
        task_metrics,
        start,
        now,
        bucket_size,
    )


async def _fetch_trend_data_points(
    app_state: AppState,
    metric: TrendMetric,
    start: datetime,
    now: datetime,
    bucket_size: BucketSize,
) -> tuple[TrendDataPoint, ...]:
    """Fetch and bucket trend data points for a given metric.

    Args:
        app_state: Application state.
        metric: Which metric to compute.
        start: Period start.
        now: Current time (period end).
        bucket_size: Bucket granularity.

    Returns:
        Bucketed data points for the metric.
    """
    if metric == TrendMetric.SPEND:
        records = await collect_all_records(
            require_service(
                app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
            ),
            start=start,
            end=now,
        )
        return bucket_cost_records(records, start, now, bucket_size)

    if metric in (TrendMetric.TASKS_COMPLETED, TrendMetric.SUCCESS_RATE):
        try:
            task_metrics = require_service(
                app_state.slice(HrStateSlice).performance_tracker,
                "Performance Tracker",
            ).get_task_metrics(
                since=start,
                until=now,
            )
        except ServiceUnavailableError:
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="analytics.trends",
                error="performance_tracker_unavailable",
                metric=metric.value,
            )
            return ()
        return _bucket_task_metric_data(
            task_metrics,
            metric,
            start,
            now,
            bucket_size,
        )

    # ACTIVE_AGENTS: flat line -- no historical agent counts are
    # tracked, so report the current snapshot across all buckets.
    # Fetch the current task list so the runtime-state active count
    # reflects genuinely busy agents (not the config count).
    from synthorg.persistence.task_protocol import TaskFilterSpec  # noqa: PLC0415

    all_tasks = await persistence_of(app_state).tasks.query(TaskFilterSpec())
    active_count, _ = await _resolve_agent_counts(
        app_state,
        0,
        all_tasks=all_tasks,
    )
    return tuple(
        TrendDataPoint(timestamp=bs, value=float(active_count))
        for bs in generate_bucket_starts(start, now, bucket_size)
    )


class AnalyticsTrendsController(Controller):
    """Time-series trend data for a single metric."""

    path = "/analytics"
    tags = ("analytics",)

    @get(
        "/trends",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("analytics.trends"),
        ],
    )
    async def get_trends(
        self,
        state: State,
        period: Annotated[
            TrendPeriod,
            QueryParameter(description="Lookback period"),
        ] = TrendPeriod.SEVEN_DAYS,
        metric: Annotated[
            TrendMetric,
            QueryParameter(description="Metric to trend"),
        ] = TrendMetric.SPEND,
    ) -> ApiResponse[TrendsResponse]:
        """Return time-series trend data for a metric.

        Args:
            state: Application state.
            period: Lookback period (7d, 30d, 90d).
            metric: Metric type to trend.

        Returns:
            Bucketed trend data envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        start = now - period_to_timedelta(period)
        bucket_size = resolve_bucket_size(period)

        data_points = await _fetch_trend_data_points(
            app_state,
            metric,
            start,
            now,
            bucket_size,
        )

        logger.debug(
            ANALYTICS_TRENDS_QUERIED,
            period=period.value,
            metric=metric.value,
            bucket_size=bucket_size.value,
            data_point_count=len(data_points),
        )

        return ApiResponse(
            data=TrendsResponse(
                period=period,
                metric=metric,
                bucket_size=bucket_size,
                data_points=data_points,
            ),
        )
