"""Department health aggregation helpers + response model.

Extracted from ``departments.py`` to keep that controller focused on
Litestar route handlers.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    assert_currencies_match,
)
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.budget.trends import BucketSize, TrendDataPoint, bucket_cost_records
from synthorg.config.agent_schema import AgentConfig
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.normalization import compare_ci
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR

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
        """Percentage of agents that are active.

        Returns:
            Resulting numeric value.
        """
        if self.agent_count == 0:
            return 0.0
        return round(self.active_agent_count / self.agent_count * 100, 2)

    @model_validator(mode="after")
    def _validate_active_le_total(self) -> Self:
        """Ensure active agent count does not exceed total.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
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
    """Return agents belonging to the named department (case-insensitive).

    Returns:
        Tuple of the declared element types.
    """
    return tuple(a for a in agents if compare_ci(a.department, dept_name))


async def _resolve_active_count(
    app_state: AppState,
    dept_name: str,
) -> int:
    """Count active agents in the department via the registry.

    Returns:
        Resulting integer.
    """
    if app_state.slice(HrStateSlice).agent_registry is None:
        return 0
    try:
        registry = require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        )
        dept_agents = await registry.list_by_department(
            dept_name,
        )
        return sum(1 for a in dept_agents if a.status == AgentStatus.ACTIVE)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health",
            detail="agent_registry_query_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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

    Returns:
        Tuple of the declared element types.
    """
    if not agent_ids:
        return ()
    # Performance tracker is optional: ``assemble_department_health``
    # only catches ``ExceptionGroup`` for snapshot resolution, so a
    # ``ServiceUnavailableError`` from ``require_service`` would abort
    # the endpoint instead of returning the documented empty-snapshot
    # fallback.  Treat an unwired tracker as "no snapshots available".
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None:
        return ()
    snapshots = await tracker.get_snapshots(
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
    swallowed so a single bad agent identity does not collapse the
    department-wide health snapshot; the missing agent simply omits
    from the result.

    Returns:
        Tuple of the declared element types.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    if app_state.slice(HrStateSlice).agent_registry is None:
        return ()
    if not agent_names:
        return ()
    try:
        registry = require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        )
        identities = await registry.get_by_names(
            tuple(NotBlankStr(n) for n in agent_names),
        )
    except MemoryError, RecursionError:
        raise
    except ServiceUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 -- best-effort: log and degrade
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health.resolve_id",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    return tuple(str(i.id) for i in identities if i is not None)


def _mean_optional(values: list[float | None]) -> float | None:
    """Compute mean of non-None values, or None if all are None.

    Returns:
        The ``float`` value when present, ``None`` otherwise.
    """
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return round(math.fsum(filtered) / len(filtered), 2)


def _sparkline_start(now: datetime) -> datetime:
    """Compute the aligned start for a 7-day daily sparkline.

    Returns:
        ``datetime`` instance.
    """
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
    *,
    dept_name: NotBlankStr | None = None,
) -> DepartmentCostAggregate:
    """Filter cost records to department agents and compute totals.

    Args:
        cost_records: All cost records in scope.
        agent_id_set: Department agent ids used to filter records.
        now: Reference timestamp for the trend bucketing.
        dept_name: Optional department identifier propagated to the
            mixed-currency log + exception so operators can locate the
            offending department from the audit trail without
            correlating timestamps against the calling endpoint.

    Raises:
        MixedCurrencyAggregationError: If the matched cost records span
            more than one currency.  Cost summation across currencies
            is meaningless without an FX policy and is rejected at the
            aggregator boundary; the caller must scope the input to a
            single currency window.

    Returns:
        ``DepartmentCostAggregate`` instance.
    """
    dept_records = tuple(r for r in cost_records if r.agent_id in agent_id_set)
    currency = assert_currencies_match(
        (r.currency for r in dept_records),
        project_id=dept_name,
    )
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
    """Build a minimal DepartmentHealth for when queries fail.

    Returns:
        ``DepartmentHealth`` instance.
    """
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

    Returns:
        ``DepartmentHealth`` instance.
    """
    agent_id_set = frozenset(agent_ids)
    aggregate = _aggregate_dept_cost(
        cost_records,
        agent_id_set,
        now,
        dept_name=NotBlankStr(dept_name),
    )
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

    The first stage queries active agent count, cost records, and
    agent ID resolution in parallel via TaskGroup.  If the first stage
    fails, returns a degraded health response with zeroed metrics.
    The second stage fetches performance snapshots (depends on the
    agent IDs resolved by the first stage).

    Returns:
        ``DepartmentHealth`` instance.

    Raises:
        fatal: Raised on the corresponding failure path.
    """
    agent_count = len(dept_agents)
    agent_names = tuple(str(a.name) for a in dept_agents)

    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)

    try:
        # Resolve ``cost_tracker`` inside the try so an unwired tracker
        # takes the documented degraded-fallback path (zeroed metrics)
        # rather than aborting the whole request.  Both the unwired-
        # service case and the TaskGroup-fan-out failure case feed into
        # the same degraded response below.
        cost_tracker = require_service(
            app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
        )
        async with asyncio.TaskGroup() as tg:
            t_active = tg.create_task(
                _resolve_active_count(app_state, dept_name),
            )
            t_cost = tg.create_task(
                collect_all_records(
                    cost_tracker,
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
        )
        return _build_degraded_health(dept_name, agent_count, now, currency=currency)
    except ServiceUnavailableError as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health",
            department=dept_name,
            error_type=type(exc).__name__,
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
