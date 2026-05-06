"""Analytics controller -- derived read-only metrics."""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, NamedTuple

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.billing import billing_period_start
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.trends import (
    BucketSize,
    ForecastPoint,
    TrendDataPoint,
    TrendMetric,
    TrendPeriod,
    bucket_cost_records,
    bucket_success_rate,
    bucket_task_completions,
    generate_bucket_starts,
    period_to_timedelta,
    project_daily_spend,
    resolve_bucket_size,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import TaskStatus
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import (
    ANALYTICS_FORECAST_QUERIED,
    ANALYTICS_OVERVIEW_QUERIED,
    ANALYTICS_TRENDS_QUERIED,
)
from synthorg.observability.events.api import API_REQUEST_ERROR

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from synthorg.hr.performance.models import TaskMetricRecord

logger = get_logger(__name__)


# ── Response models ────────────────────────────────────────────


class OverviewMetrics(BaseModel):
    """High-level analytics overview.

    Attributes:
        total_tasks: Total number of tasks.
        tasks_by_status: Task counts grouped by status.
        total_agents: Number of configured agents.
        total_cost: Total cost across all records.
        budget_remaining: Remaining budget for the current period.
        budget_used_percent: Percentage of monthly budget used.
            Values above 100.0 indicate budget overrun.
        cost_7d_trend: Daily spend sparkline for the last 7 days.
        active_agents_count: Agents currently executing an in-progress task.
        idle_agents_count: Employed agents not currently executing a task.
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_tasks: int = Field(ge=0, description="Total number of tasks")
    tasks_by_status: dict[str, int] = Field(
        description="Task counts by status (keys are TaskStatus values)",
    )
    total_agents: int = Field(ge=0, description="Number of configured agents")
    total_cost: float = Field(
        ge=0.0, description="Total cost in the configured currency"
    )
    budget_remaining: float = Field(
        ge=0.0,
        description="Remaining budget in the configured currency",
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )
    budget_used_percent: float = Field(
        ge=0.0,
        description="Percentage of monthly budget used (>100 = overrun)",
    )
    cost_7d_trend: tuple[TrendDataPoint, ...] = Field(
        description="Daily spend sparkline for the last 7 days",
    )
    active_agents_count: int = Field(
        ge=0,
        description="Agents currently executing an in-progress task",
    )
    idle_agents_count: int = Field(
        ge=0,
        description="Employed agents not currently executing a task",
    )


class TrendsResponse(BaseModel):
    """Time-series trend data for a single metric.

    Attributes:
        period: Lookback period used.
        metric: Metric type queried.
        bucket_size: Time granularity of data points.
        data_points: Bucketed time-series data.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    period: TrendPeriod = Field(description="Lookback period")
    metric: TrendMetric = Field(description="Metric type queried")
    bucket_size: BucketSize = Field(description="Bucket granularity")
    data_points: tuple[TrendDataPoint, ...] = Field(
        description="Bucketed time-series data points",
    )


class ForecastResponse(BaseModel):
    """Budget spend projection.

    Attributes:
        horizon_days: Projection horizon in days.
        projected_total: Projected total spend over the horizon.
        daily_projections: Per-day cumulative spend projections.
        days_until_exhausted: Days until budget exhaustion.
        confidence: Confidence score based on data density.
        avg_daily_spend: Average daily spend used for projection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    horizon_days: int = Field(ge=1, description="Projection horizon")
    projected_total: float = Field(
        ge=0.0,
        description="Projected total spend over the horizon",
    )
    daily_projections: tuple[ForecastPoint, ...] = Field(
        description="Per-day cumulative spend projections",
    )
    days_until_exhausted: int | None = Field(
        default=None,
        ge=0,
        description="Days until budget exhaustion",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score based on data density",
    )
    avg_daily_spend: float = Field(
        ge=0.0,
        description="Average daily spend used for projection",
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )


# ── Helpers ────────────────────────────────────────────────────


class _BudgetContext(NamedTuple):
    """Resolved budget state for the current billing period."""

    monthly: float
    remaining: float
    used_percent: float


async def _resolve_budget_context(
    app_state: AppState,
    fallback_total_cost: float = 0.0,
    *,
    now: datetime | None = None,
) -> _BudgetContext:
    """Compute budget remaining and usage percentage.

    Args:
        app_state: Application state.
        fallback_total_cost: Total cost to use if period query fails.
        now: Upper bound for cost query (exclusive). Defaults to
            current UTC time.

    Returns:
        Budget context with monthly, remaining, and used_percent.
    """
    budget_config = app_state.cost_tracker.budget_config
    monthly = budget_config.total_monthly if budget_config else 0.0
    if budget_config is None or monthly <= 0:
        return _BudgetContext(monthly=0.0, remaining=0.0, used_percent=0.0)

    end = now or datetime.now(UTC)
    period_start = billing_period_start(budget_config.reset_day)
    try:
        period_cost = await app_state.cost_tracker.get_total_cost(
            start=period_start,
            end=end,
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="analytics.budget_context",
            error="period_cost_query_failed",
        )
        period_cost = fallback_total_cost

    used_pct = round(period_cost / monthly * 100, BUDGET_ROUNDING_PRECISION)
    remaining = round(max(monthly - period_cost, 0.0), BUDGET_ROUNDING_PRECISION)
    return _BudgetContext(
        monthly=monthly,
        remaining=remaining,
        used_percent=used_pct,
    )


async def _resolve_agent_counts(
    app_state: AppState,
    config_agent_count: int,
    all_tasks: Sequence[Any] | None = None,
) -> tuple[int, int]:
    """Resolve active and idle agent counts.

    "Active" means **currently busy executing a task** -- an agent is
    counted as active if they are assigned to at least one task whose
    status is ``IN_PROGRESS``.  "Idle" is everyone else on the payroll
    (employed agents from :meth:`AgentRegistryService.list_active`
    minus the busy ones).

    The older semantics treated every agent with employment status
    ``ACTIVE`` as "active", which conflated HR lifecycle with runtime
    state and produced the surprising "4 active / 0 idle / 0 tasks"
    display.  The runtime-state definition matches operator intuition:
    if no tasks are in progress, no agents are active.

    Uses :class:`AgentRegistryService` when available to resolve
    employed agents.  When the registry is unavailable, returns
    ``(0, config_agent_count)`` because without the HR registry
    there is no way to tell which agents are employed vs. merely
    assigned to tasks, and treating every agent as idle is the
    safest conservative display.

    Args:
        app_state: Application state.
        config_agent_count: Fallback total from config (caller
            typically passes ``len(agents)``).
        all_tasks: The full task list already fetched by the caller.
            Required for the runtime-state computation; when omitted
            or empty, every employed agent is reported as idle.

    Returns:
        Tuple of (active_count, idle_count).
    """
    if not app_state.has_agent_registry:
        if not all_tasks:
            logger.debug(
                ANALYTICS_OVERVIEW_QUERIED,
                note="no agent registry -- all agents reported as idle",
                config_agent_count=config_agent_count,
            )
            return 0, config_agent_count
        # Without the registry we cannot distinguish employed agents,
        # but we can still count busy assignees from the task list.
        active_ids: set[str] = set()
        for task in all_tasks:
            if task.status == TaskStatus.IN_PROGRESS and task.assigned_to:
                active_ids.add(task.assigned_to)
        active = len(active_ids)
        idle = max(config_agent_count - active, 0)
        logger.debug(
            ANALYTICS_OVERVIEW_QUERIED,
            note="no agent registry -- derived active from tasks",
            active=active,
            idle=idle,
            config_agent_count=config_agent_count,
        )
        return active, idle
    try:
        employed = await app_state.agent_registry.list_active()
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="analytics.resolve_agent_counts",
            error="agent_registry_query_failed",
        )
        return 0, config_agent_count

    employed_ids = {str(agent.id) for agent in employed}
    # Both None (not provided) and [] (no tasks) yield 0 active.
    if not all_tasks:
        logger.debug(
            ANALYTICS_OVERVIEW_QUERIED,
            note="no tasks provided -- all employed agents reported as idle",
            employed_count=len(employed_ids),
        )
        return 0, len(employed_ids)

    busy_ids: set[str] = set()
    for task in all_tasks:
        if (
            task.status == TaskStatus.IN_PROGRESS
            and task.assigned_to
            and task.assigned_to in employed_ids
        ):
            busy_ids.add(task.assigned_to)
    active = len(busy_ids)
    idle = max(len(employed_ids) - active, 0)
    return active, idle


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
        records = await app_state.cost_tracker.get_records(
            start=start,
            end=now,
        )
        return bucket_cost_records(records, start, now, bucket_size)

    if metric in (TrendMetric.TASKS_COMPLETED, TrendMetric.SUCCESS_RATE):
        try:
            task_metrics = app_state.performance_tracker.get_task_metrics(
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
    all_tasks = await app_state.persistence.tasks.list_tasks()
    active_count, _ = await _resolve_agent_counts(
        app_state,
        0,
        all_tasks=all_tasks,
    )
    return tuple(
        TrendDataPoint(timestamp=bs, value=float(active_count))
        for bs in generate_bucket_starts(start, now, bucket_size)
    )


async def _assemble_overview(  # noqa: PLR0913
    app_state: AppState,
    *,
    all_tasks: Sequence[Any],
    total_cost: float,
    agents: Sequence[Any],
    records_7d: Sequence[Any],
    now: datetime,
) -> OverviewMetrics:
    """Build overview metrics from parallel query results.

    Args:
        app_state: Application state.
        all_tasks: All tasks from persistence.
        total_cost: Total cost across all records.
        agents: Agent configurations.
        records_7d: Cost records from the last 7 days.
        now: Current time reference.

    Returns:
        Populated overview metrics.
    """
    counts = Counter(t.status.value for t in all_tasks)
    by_status = {s.value: counts.get(s.value, 0) for s in TaskStatus}

    budget_cfg = await app_state.config_resolver.get_budget_config()
    budget = await _resolve_budget_context(app_state, total_cost, now=now)
    # Overview sparkline uses daily buckets intentionally (not hourly
    # like /trends?period=7d) to produce a compact 7-point sparkline.
    # Align start to midnight 6 days ago so we get exactly 7 buckets.
    sparkline_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=6)
    cost_7d = bucket_cost_records(
        records_7d,
        sparkline_start,
        now,
        BucketSize.DAY,
    )
    active, idle = await _resolve_agent_counts(
        app_state,
        len(agents),
        all_tasks=all_tasks,
    )

    logger.debug(
        ANALYTICS_OVERVIEW_QUERIED,
        total_tasks=len(all_tasks),
        total_cost=total_cost,
        active_agents=active,
    )

    return OverviewMetrics(
        total_tasks=len(all_tasks),
        tasks_by_status=by_status,
        total_agents=len(agents),
        total_cost=total_cost,
        budget_remaining=budget.remaining,
        budget_used_percent=budget.used_percent,
        cost_7d_trend=cost_7d,
        active_agents_count=active,
        idle_agents_count=idle,
        currency=budget_cfg.currency,
    )


# ── Controller ─────────────────────────────────────────────────


class AnalyticsController(Controller):
    """Derived analytics and metrics."""

    path = "/analytics"
    tags = ("analytics",)

    @get(
        "/overview",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("analytics.overview"),
        ],
    )
    async def get_overview(
        self,
        state: State,
    ) -> ApiResponse[OverviewMetrics]:
        """Return high-level metrics overview.

        Includes task counts, cost totals, budget status, 7-day
        spend sparkline, and agent activity counts.

        Args:
            state: Application state.

        Returns:
            Overview metrics envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)

        try:
            async with asyncio.TaskGroup() as tg:
                t_tasks = tg.create_task(
                    app_state.persistence.tasks.list_tasks(),
                )
                t_cost = tg.create_task(
                    app_state.cost_tracker.get_total_cost(),
                )
                t_agents = tg.create_task(
                    app_state.config_resolver.get_agents(),
                )
                t_7d = tg.create_task(
                    app_state.cost_tracker.get_records(
                        start=now - timedelta(days=7),
                        end=now,
                    ),
                )
        except ExceptionGroup as eg:
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="analytics.overview",
                error_count=len(eg.exceptions),
            )
            msg = "analytics overview temporarily unavailable"
            raise ServiceUnavailableError(msg) from eg

        return ApiResponse(
            data=await _assemble_overview(
                app_state,
                all_tasks=t_tasks.result(),
                total_cost=t_cost.result(),
                agents=t_agents.result(),
                records_7d=t_7d.result(),
                now=now,
            ),
        )

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
            Parameter(description="Lookback period"),
        ] = TrendPeriod.SEVEN_DAYS,
        metric: Annotated[
            TrendMetric,
            Parameter(description="Metric to trend"),
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

    @get(
        "/forecast",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("analytics.forecast"),
        ],
    )
    async def get_forecast(
        self,
        state: State,
        horizon_days: Annotated[
            int,
            Parameter(
                ge=1,
                le=90,
                description="Projection horizon in days",
            ),
        ] = 14,
    ) -> ApiResponse[ForecastResponse]:
        """Return budget spend projection.

        Fetches records from the lookback period (equal to
        horizon_days), then computes average daily spend from
        the span of records found. Confidence reflects data
        density within the lookback window.

        Args:
            state: Application state.
            horizon_days: Number of days to project forward.

        Returns:
            Forecast data envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        lookback_start = now - timedelta(days=horizon_days)

        records = await app_state.cost_tracker.get_records(
            start=lookback_start,
            end=now,
        )
        budget = await _resolve_budget_context(app_state, now=now)

        forecast = project_daily_spend(
            records,
            horizon_days=horizon_days,
            budget_total_monthly=budget.monthly,
            budget_remaining=budget.remaining,
            now=now,
        )

        logger.debug(
            ANALYTICS_FORECAST_QUERIED,
            horizon_days=horizon_days,
            projected_total=forecast.projected_total,
            days_until_exhausted=forecast.days_until_exhausted,
        )

        budget_cfg = await app_state.config_resolver.get_budget_config()
        return ApiResponse(
            data=ForecastResponse(
                horizon_days=horizon_days,
                projected_total=forecast.projected_total,
                daily_projections=forecast.daily_projections,
                days_until_exhausted=forecast.days_until_exhausted,
                confidence=forecast.confidence,
                avg_daily_spend=forecast.avg_daily_spend,
                currency=budget_cfg.currency,
            ),
        )
