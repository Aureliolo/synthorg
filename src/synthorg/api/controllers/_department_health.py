# module-kind: service
"""Department health aggregation helpers + response model.

Extracted from ``departments.py`` to keep that controller focused on
Litestar route handlers. Fans out across the agent registry, cost tracker,
performance snapshots, and real task-outcome metrics to assemble a single
honest health response.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Final, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg._core.features import require_service
from synthorg.api.api_core_state import analytics_read_service_of
from synthorg.api.controllers._department_health_outcomes import (
    health_from_outcomes,
    resolve_task_outcomes,
)
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
from synthorg.core.task import Task
from synthorg.core.task_activity import busy_agent_ids
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR

logger = get_logger(__name__)

# Fallbacks when a caller does not pass the settings-resolved values (the
# controller always does; these cover direct/test callers). Each mirrors its
# ``hr.department_health_*`` setting default so a direct caller behaves like
# production.
_DEFAULT_HEALTH_WINDOW_DAYS: Final[int] = 7
_DEFAULT_HEALTH_MIN_RUNS: Final[int] = 3


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
        total_runs: Terminal task runs by this department in the health window.
        task_success_rate: Fraction of terminal runs that produced output
            (0-1), or None below the minimum-runs gate.
        health_score: Derived (computed_field) from task_success_rate as a
            0-100 score; None when task_success_rate is None (no-data).
        utilization_percent: Derived (computed_field) from
            active_agent_count / agent_count.
        utilization_degraded: True when the in-flight-task query failed, so
            active_agent_count (and thus utilization_percent) is a floor, not
            a measured value; the dashboard renders utilisation as "unknown"
            rather than a confident number.
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
    total_runs: int = Field(
        default=0,
        ge=0,
        description="Terminal task runs by this department in the health window",
    )
    utilization_degraded: bool = Field(
        default=False,
        description=(
            "True when the in-flight-task query failed; utilization_percent is"
            " then a floor, not a measured value."
        ),
    )
    task_success_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of terminal runs that genuinely produced output (empty"
            " and failed runs count as non-success). None below the"
            " minimum-runs gate (no honest signal yet)."
        ),
    )

    @computed_field
    @property
    def health_score(self) -> float | None:
        """Real department health: task-outcome success rate as a 0-100 score.

        Derived from ``task_success_rate`` so the two never drift. ``None``
        when there is insufficient activity to judge (``task_success_rate`` is
        ``None``), which the dashboard renders as an explicit no-data state
        rather than a misleading full-health number.
        """
        if self.task_success_rate is None:
            return None
        return round(self.task_success_rate * 100, 2)

    @computed_field
    @property
    def utilization_percent(self) -> float:
        """Roster utilisation: percentage of agents currently active.

        A lifecycle/roster ratio, not a measure of work quality. The
        dashboard shows it as utilisation, never as health (which is
        ``health_score``, derived from real task outcomes).
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


async def _resolve_inprogress_tasks(
    app_state: AppState,
) -> tuple[tuple[Task, ...], bool]:
    """Fetch the in-flight tasks used to derive department utilisation.

    Utilisation is a runtime measure: an agent counts as active only while
    it is executing a task (assignee of an ``IN_PROGRESS`` task), never by
    its ``AgentStatus`` lifecycle flag. A read failure degrades to no
    in-flight work (zero utilisation) rather than collapsing the whole
    health response, so costs and performance still surface, but the degraded
    flag rides along so the caller can mark utilisation "unknown" instead of a
    confident zero.

    Returns:
        ``(tasks, degraded)``: every ``IN_PROGRESS`` task and ``False`` on
        success, or ``((), True)`` on read failure.
    """
    try:
        return await analytics_read_service_of(app_state).list_in_progress(), False
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health",
            detail="inprogress_tasks_query_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return (), True


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
    # Performance tracker is optional: treat an unwired tracker as "no
    # snapshots available" here rather than raising, so a missing tracker
    # yields the documented empty-snapshot fallback.
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

    The whole first stage failed, so utilisation is unknown (not a measured
    zero): mark it degraded.

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
        utilization_degraded=True,
        currency=currency,
    )


def _build_health_from_data(  # noqa: PLR0913, PLR0917
    dept_name: str,
    agent_count: int,
    active_count: int,
    cost_records: tuple[CostRecord, ...],
    agent_ids: tuple[str, ...],
    snapshots: tuple[AgentPerformanceSnapshot, ...],
    now: datetime,
    *,
    total_runs: int = 0,
    success_count: int = 0,
    min_runs: int = _DEFAULT_HEALTH_MIN_RUNS,
    utilization_degraded: bool = False,
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
    success_rate = health_from_outcomes(total_runs, success_count, min_runs)
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
        total_runs=total_runs,
        task_success_rate=success_rate,
        utilization_degraded=utilization_degraded,
        currency=aggregate.currency if aggregate.currency is not None else currency,
    )


async def assemble_department_health(  # noqa: PLR0913 -- health data sources
    app_state: AppState,
    dept_name: str,
    dept_agents: tuple[AgentConfig, ...],
    *,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    health_window_days: int = _DEFAULT_HEALTH_WINDOW_DAYS,
    health_min_runs: int = _DEFAULT_HEALTH_MIN_RUNS,
) -> DepartmentHealth:
    """Aggregate all data sources into a DepartmentHealth response.

    The first stage queries active agent count, cost records, and
    agent ID resolution in parallel via TaskGroup.  If the first stage
    fails, returns a degraded health response with zeroed metrics.
    The second stage fetches performance snapshots (depends on the
    agent IDs resolved by the first stage) and derives the honest
    ``health_score`` from the department's real task outcomes over the
    ``health_window_days`` window, gated by ``health_min_runs``.

    Returns:
        ``DepartmentHealth`` instance.

    Raises:
        fatal: Raised on the corresponding failure path.
    """
    agent_count = len(dept_agents)
    agent_names = tuple(str(a.name) for a in dept_agents)

    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    health_window_start = now - timedelta(days=health_window_days)

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
            t_tasks = tg.create_task(
                _resolve_inprogress_tasks(app_state),
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
            error=safe_error_description(exc),
        )
        return _build_degraded_health(dept_name, agent_count, now, currency=currency)

    try:
        snapshots = await _resolve_snapshots(app_state, t_ids.result())
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # Performance snapshots are optional (``avg_performance_score``
        # is nullable) -- log and fall back to an empty tuple so callers
        # still get costs + active-agent counts. ``_resolve_snapshots`` is a
        # plain await (not a TaskGroup), so a bare exception (e.g. a batch-
        # size ValueError from a large department) arrives unwrapped and must
        # be caught directly rather than as an ExceptionGroup.
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health.snapshots",
            department=dept_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        snapshots = ()

    total_runs, success_count = resolve_task_outcomes(
        app_state,
        t_ids.result(),
        window_start=health_window_start,
    )

    inprogress_tasks, utilization_degraded = t_tasks.result()
    active_count = len(busy_agent_ids(inprogress_tasks, t_ids.result()))

    return _build_health_from_data(
        dept_name=dept_name,
        agent_count=agent_count,
        active_count=active_count,
        cost_records=t_cost.result(),
        agent_ids=t_ids.result(),
        snapshots=snapshots,
        now=now,
        total_runs=total_runs,
        success_count=success_count,
        min_runs=health_min_runs,
        utilization_degraded=utilization_degraded,
        currency=currency,
    )
