"""Department health aggregation helpers + response model.

Extracted from ``departments.py`` to keep that controller focused on
Litestar route handlers.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    assert_currencies_match,
)
from synthorg.budget.trends import BucketSize, TrendDataPoint, bucket_cost_records
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import AgentStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.cost_record import CostRecord
    from synthorg.config.schema import AgentConfig
    from synthorg.hr.performance.models import AgentPerformanceSnapshot

logger = get_logger(__name__)


class DepartmentHealth(BaseModel):
    """Department-level health aggregation for dashboard display.

    Attributes:
        department_name: Department name.
        agent_count: Total agents in the department.
        active_agent_count: Number of active agents.
        avg_performance_score: Mean quality score across agents.
        department_cost_7d: Total cost in the last 7 days.
        cost_trend: Daily spend sparkline for the last 7 days.
        collaboration_score: Mean collaboration score across agents.
        utilization_percent: Derived (computed_field) from
            active_agent_count / agent_count.
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    department_name: NotBlankStr = Field(description="Department name")
    agent_count: int = Field(ge=0, description="Total agents")
    active_agent_count: int = Field(ge=0, description="Active agents")
    currency: CurrencyCode = Field(
        default=DEFAULT_CURRENCY,
        description="ISO 4217 currency code",
    )
    avg_performance_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Mean quality score (0-10)",
    )
    department_cost_7d: float = Field(
        ge=0.0,
        description="Total cost in last 7 days",
    )
    cost_trend: tuple[TrendDataPoint, ...] = Field(
        description="7-day daily spend sparkline",
    )
    collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Mean collaboration score (0-10)",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def utilization_percent(self) -> float:
        """Percentage of agents that are active."""
        if self.agent_count == 0:
            return 0.0
        return round(self.active_agent_count / self.agent_count * 100, 2)

    @model_validator(mode="after")
    def _validate_active_le_total(self) -> Self:
        """Ensure active agent count does not exceed total."""
        if self.active_agent_count > self.agent_count:
            msg = (
                f"active_agent_count ({self.active_agent_count}) "
                f"exceeds agent_count ({self.agent_count})"
            )
            raise ValueError(msg)
        return self


def filter_agents_by_department(
    agents: tuple[AgentConfig, ...],
    dept_name: str,
) -> tuple[AgentConfig, ...]:
    """Return agents belonging to the named department (case-insensitive)."""
    lower = dept_name.lower()
    return tuple(a for a in agents if a.department.lower() == lower)


async def _resolve_active_count(
    app_state: AppState,
    dept_name: str,
) -> int:
    """Count active agents in the department via the registry."""
    if not app_state.has_agent_registry:
        return 0
    try:
        dept_agents = await app_state.agent_registry.list_by_department(
            dept_name,
        )
        return sum(1 for a in dept_agents if a.status == AgentStatus.ACTIVE)
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health",
            error="agent_registry_query_failed",
            exc_info=True,
        )
        return 0


async def _resolve_snapshots(
    app_state: AppState,
    agent_ids: tuple[str, ...],
) -> tuple[AgentPerformanceSnapshot, ...]:
    """Fetch performance snapshots for the given agent IDs.

    Delegates to ``PerformanceTracker.get_snapshots`` which computes
    snapshots in a single batch and returns ``None`` for any agent
    whose snapshot cannot be computed (insufficient data, strategy
    errors).  Missing snapshots are filtered out here so callers see
    only the successfully computed ones.
    """
    if not agent_ids:
        return ()
    snapshots = await app_state.performance_tracker.get_snapshots(
        tuple(NotBlankStr(a) for a in agent_ids),
    )
    return tuple(s for s in snapshots if s is not None)


async def _resolve_agent_ids(
    app_state: AppState,
    agent_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Map agent names to IDs via the registry in a single batch call.

    ``ServiceUnavailableError`` is allowed to propagate so the outer
    ``assemble_department_health`` handler can surface registry outage
    as a degraded response instead of silently reporting zero agents.
    Other exceptions (e.g. unexpected registry bugs) are logged and
    swallowed because per-name lookup failures used to be tolerated in
    the previous per-agent fan-out.
    """
    if not app_state.has_agent_registry:
        return ()
    if not agent_names:
        return ()
    try:
        identities = await app_state.agent_registry.get_by_names(
            tuple(NotBlankStr(n) for n in agent_names),
        )
    except MemoryError, RecursionError:
        raise
    except ServiceUnavailableError:
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health.resolve_id",
            exc_info=True,
        )
        return ()
    return tuple(str(i.id) for i in identities if i is not None)


def _mean_optional(values: list[float | None]) -> float | None:
    """Compute mean of non-None values, or None if all are None."""
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return round(math.fsum(filtered) / len(filtered), 2)


def _sparkline_start(now: datetime) -> datetime:
    """Compute the aligned start for a 7-day daily sparkline."""
    return now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=6)


class DepartmentCostAggregate(NamedTuple):
    """Aggregated cost view for a department.

    Returned by :func:`_aggregate_dept_cost`.  Named fields prevent
    callers from mis-ordering ``total_cost`` / ``currency`` / ``trend``
    when destructuring -- a code smell with bare 3-tuples of mixed
    types.

    Attributes:
        total_cost: Sum of cost across the matched cost records,
            denominated in ``currency``.
        currency: ISO 4217 code shared by every contributing record;
            ``None`` only when no records matched.
        trend: 7-day daily spend sparkline.
    """

    total_cost: float
    currency: CurrencyCode | None
    trend: tuple[TrendDataPoint, ...]


def _aggregate_dept_cost(
    cost_records: tuple[CostRecord, ...],
    agent_id_set: frozenset[str],
    now: datetime,
) -> DepartmentCostAggregate:
    """Filter cost records to department agents and compute totals.

    Args:
        cost_records: All cost records in scope.
        agent_id_set: Department agent ids used to filter records.
        now: Reference timestamp for the trend bucketing.

    Raises:
        MixedCurrencyAggregationError: If the matched cost records span
            more than one currency.  Cost summation across currencies
            is meaningless without an FX policy and is rejected at the
            aggregator boundary; the caller must scope the input to a
            single currency window.
    """
    dept_records = tuple(r for r in cost_records if r.agent_id in agent_id_set)
    currency = assert_currencies_match(r.currency for r in dept_records)
    total = round(
        math.fsum(r.cost for r in dept_records),
        BUDGET_ROUNDING_PRECISION,
    )
    trend = bucket_cost_records(
        dept_records,
        _sparkline_start(now),
        now,
        BucketSize.DAY,
    )
    return DepartmentCostAggregate(total_cost=total, currency=currency, trend=trend)


def _build_degraded_health(
    dept_name: str,
    agent_count: int,
    now: datetime,
    *,
    currency: CurrencyCode = DEFAULT_CURRENCY,
) -> DepartmentHealth:
    """Build a minimal DepartmentHealth for when queries fail."""
    return DepartmentHealth(
        department_name=dept_name,
        agent_count=agent_count,
        active_agent_count=0,
        department_cost_7d=0.0,
        cost_trend=bucket_cost_records(
            (),
            _sparkline_start(now),
            now,
            BucketSize.DAY,
        ),
        currency=currency,
    )


def _build_health_from_data(  # noqa: PLR0913
    dept_name: str,
    agent_count: int,
    active_count: int,
    cost_records: tuple[CostRecord, ...],
    agent_ids: tuple[str, ...],
    snapshots: tuple[AgentPerformanceSnapshot, ...],
    now: datetime,
    *,
    currency: CurrencyCode = DEFAULT_CURRENCY,
) -> DepartmentHealth:
    """Build DepartmentHealth from resolved query results.

    Raises:
        MixedCurrencyAggregationError: Propagated from
            ``_aggregate_dept_cost`` if the department's cost records
            span more than one currency.
    """
    agent_id_set = frozenset(agent_ids)
    aggregate = _aggregate_dept_cost(cost_records, agent_id_set, now)
    return DepartmentHealth(
        department_name=dept_name,
        agent_count=agent_count,
        active_agent_count=active_count,
        avg_performance_score=_mean_optional(
            [s.overall_quality_score for s in snapshots],
        ),
        department_cost_7d=aggregate.total_cost,
        cost_trend=aggregate.trend,
        collaboration_score=_mean_optional(
            [s.overall_collaboration_score for s in snapshots],
        ),
        currency=aggregate.currency if aggregate.currency is not None else currency,
    )


async def assemble_department_health(
    app_state: AppState,
    dept_name: str,
    dept_agents: tuple[AgentConfig, ...],
    *,
    currency: CurrencyCode = DEFAULT_CURRENCY,
) -> DepartmentHealth:
    """Aggregate all data sources into a DepartmentHealth response.

    Phase 1 queries active agent count, cost records, and agent ID
    resolution in parallel via TaskGroup.  If Phase 1 fails, returns
    a degraded health response with zeroed metrics.  Phase 2 fetches
    performance snapshots (depends on resolved agent IDs from Phase 1).
    """
    agent_count = len(dept_agents)
    agent_names = tuple(str(a.name) for a in dept_agents)

    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)

    try:
        async with asyncio.TaskGroup() as tg:
            t_active = tg.create_task(
                _resolve_active_count(app_state, dept_name),
            )
            t_cost = tg.create_task(
                app_state.cost_tracker.get_records(
                    start=seven_days_ago,
                    end=now,
                ),
            )
            t_ids = tg.create_task(
                _resolve_agent_ids(app_state, agent_names),
            )
    except ExceptionGroup as eg:
        fatal = eg.subgroup((MemoryError, RecursionError))
        if fatal is not None:
            raise fatal from eg
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health",
            department=dept_name,
            error_count=len(eg.exceptions),
            exc_info=True,
        )
        return _build_degraded_health(dept_name, agent_count, now, currency=currency)

    try:
        snapshots = await _resolve_snapshots(app_state, t_ids.result())
    except ExceptionGroup as eg:
        fatal = eg.subgroup((MemoryError, RecursionError))
        if fatal is not None:
            raise fatal from eg
        # Performance snapshots are optional (``avg_performance_score``
        # is nullable) -- log and fall back to an empty tuple so callers
        # still get costs + active-agent counts.
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health.snapshots",
            department=dept_name,
            error_count=len(eg.exceptions),
            exc_info=True,
        )
        snapshots = ()

    return _build_health_from_data(
        dept_name=dept_name,
        agent_count=agent_count,
        active_count=t_active.result(),
        cost_records=t_cost.result(),
        agent_ids=t_ids.result(),
        snapshots=snapshots,
        now=now,
        currency=currency,
    )
