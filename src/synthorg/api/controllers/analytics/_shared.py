"""Response models and cross-endpoint helpers for analytics.

Holds the three analytics response DTOs (``OverviewMetrics``,
``TrendsResponse``, ``ForecastResponse``) plus the helpers shared by
more than one endpoint: ``_resolve_budget_context`` (overview +
forecast) and ``_resolve_agent_counts`` (overview + the active-agents
trend). The per-endpoint assembly/bucketing helpers live with their
controllers.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.budget.billing import billing_period_start
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.trends import (
    BucketSize,
    ForecastPoint,
    TrendDataPoint,
    TrendMetric,
    TrendPeriod,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.analytics import ANALYTICS_OVERVIEW_QUERIED
from synthorg.observability.events.api import API_REQUEST_ERROR

if TYPE_CHECKING:
    from synthorg.core.task import Task
from collections.abc import Sequence

logger = get_logger(__name__)
_DEFAULT_HORIZON_DAYS: Final[int] = 14


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
    budget_config = require_service(
        app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
    ).budget_config
    monthly = budget_config.total_monthly if budget_config else 0.0
    if budget_config is None or monthly <= 0:
        return _BudgetContext(monthly=0.0, remaining=0.0, used_percent=0.0)

    end = now or datetime.now(UTC)
    period_start = billing_period_start(budget_config.reset_day)
    try:
        period_cost = await require_service(
            app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
        ).get_total_cost(
            start=period_start,
            end=end,
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="analytics.budget_context",
            detail="period_cost_query_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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
    all_tasks: Sequence[Task] | None = None,
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
    if app_state.slice(HrStateSlice).agent_registry is None:
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
        employed = await require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        ).list_active()
    except Exception as exc:
        reraise_critical(exc)
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
