# module-kind: controller
"""Analytics overview endpoint at /analytics/overview."""

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import analytics_read_service_of
from synthorg.api.controllers.analytics._overview_trends import (
    approvals_raised_per_day,
    roster_size_per_day,
    tasks_completed_per_day,
)
from synthorg.api.controllers.analytics._shared import (
    OverviewMetrics,
    TaskOutcomeCounts,
    _resolve_agent_counts,
    _resolve_budget_context,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.approval.state import approval_store_of
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency_resolver import resolve_currency
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.budget.trends import BucketSize, bucket_cost_records
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.state import performance_tracker_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.analytics import (
    ANALYTICS_OVERVIEW_OUTCOME_DEGRADED,
    ANALYTICS_OVERVIEW_QUERIED,
    ANALYTICS_OVERVIEW_TREND_SOURCE_DEGRADED,
)
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

# Page size for the batched artifact-presence scan. One filtered query
# (task_id IN ...) with offset paging replaces one read per reviewable task, so
# a polled /overview never fans out an N+1 read path. Internal, not a knob.
_ARTIFACT_SCAN_PAGE_SIZE: Final[int] = 500


async def _task_ids_with_artifacts(
    backend: PersistenceBackend, task_ids: frozenset[str]
) -> set[str]:
    """Return the subset of ``task_ids`` that produced at least one artifact.

    One filtered ``task_id IN (...)`` query (offset-paged over a stable filter),
    short-circuiting once every task id has been seen, so classifying many tasks
    costs one query path rather than one per task.

    Returns:
        The task ids that have at least one artifact.
    """
    present: set[str] = set()
    spec = ArtifactFilterSpec(task_ids=frozenset(NotBlankStr(t) for t in task_ids))
    offset = 0
    # Bounded offset pagination over a finite artifact table: terminates on a
    # partial/empty page or once every task id is seen -- not a daemon loop.
    # lint-allow: long-running-loop-kill-switch -- bounded pagination, see above
    while True:
        batch = await backend.artifacts.query(
            spec, limit=_ARTIFACT_SCAN_PAGE_SIZE, offset=offset
        )
        present.update(str(art.task_id) for art in batch)
        if present >= task_ids or len(batch) < _ARTIFACT_SCAN_PAGE_SIZE:
            # Every reviewable task accounted for, or the last (partial) page
            # is exhausted.
            break
        offset += _ARTIFACT_SCAN_PAGE_SIZE
    return present


async def _resolve_task_outcomes(
    app_state: AppState, all_tasks: Sequence[Task]
) -> TaskOutcomeCounts:
    """Break terminal tasks into succeeded / empty / failed outcome counts.

    ``failed`` comes straight from the task status; ``empty`` vs ``succeeded``
    is the real produced-artifact split (a reviewable task with no artifact is
    empty). When the artifact presence is unavailable (no backend or a read
    fault) a finished run is credited as succeeded rather than fabricating an
    empty outcome, and the degradation is logged. This is a per-request display
    value, never persisted, so an optimistic credit here (unlike the observer's
    persisted metric record) cannot corrupt history.

    Returns:
        The outcome breakdown across terminal tasks.
    """
    failed = sum(1 for t in all_tasks if t.status == TaskStatus.FAILED)
    reviewable = [
        t for t in all_tasks if t.status in (TaskStatus.IN_REVIEW, TaskStatus.COMPLETED)
    ]
    if not reviewable:
        return TaskOutcomeCounts(failed=failed)
    try:
        backend = persistence_of(app_state)
    except ServiceUnavailableError as exc:
        logger.warning(
            ANALYTICS_OVERVIEW_OUTCOME_DEGRADED,
            reason="persistence_unavailable",
            reviewable=len(reviewable),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return TaskOutcomeCounts(succeeded=len(reviewable), failed=failed)

    reviewable_ids = frozenset(str(t.id) for t in reviewable)
    try:
        produced_ids = await _task_ids_with_artifacts(backend, reviewable_ids)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            ANALYTICS_OVERVIEW_OUTCOME_DEGRADED,
            reason="artifact_count_unavailable",
            reviewable=len(reviewable),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Unknown artifact presence: credit finished runs as succeeded rather
        # than fabricate empty outcomes.
        return TaskOutcomeCounts(succeeded=len(reviewable), failed=failed)

    empty = sum(1 for tid in reviewable_ids if tid not in produced_ids)
    succeeded = len(reviewable) - empty
    return TaskOutcomeCounts(succeeded=succeeded, empty=empty, failed=failed)


def _log_trend_source_unavailable(source: str) -> None:
    """Log a degraded trend source.

    Keeps a flat-zero sparkline distinguishable from genuine zero activity.
    """
    logger.debug(
        ANALYTICS_OVERVIEW_TREND_SOURCE_DEGRADED,
        source=source,
        note="trend source unavailable; sparkline degrades to empty",
    )


async def _collect_trend_sources(
    app_state: AppState,
    now: datetime,
) -> tuple[
    tuple[TaskMetricRecord, ...],
    tuple[AgentLifecycleEvent, ...],
    tuple[ApprovalItem, ...],
]:
    """Fetch the sparkline sources, degrading each to empty on failure.

    The card sparklines are decorative context; an unwired performance
    tracker, persistence backend, or approval store must not take the
    whole overview down with it.

    Returns:
        Task metric records, lifecycle events, and approval items for
        the trailing 7-day window (each empty when its service is
        unavailable).
    """
    since = now - timedelta(days=7)
    metrics: tuple[TaskMetricRecord, ...] = ()
    events: tuple[AgentLifecycleEvent, ...] = ()
    approvals: tuple[ApprovalItem, ...] = ()
    try:
        metrics = performance_tracker_of(app_state).get_task_metrics(since=since)
    except ServiceUnavailableError:
        _log_trend_source_unavailable("performance_tracker")
    try:
        events = await persistence_of(app_state).lifecycle_events.list_events(
            since=since,
        )
    except ServiceUnavailableError:
        _log_trend_source_unavailable("lifecycle_events")
    try:
        approvals = await approval_store_of(app_state).list_items(created_since=since)
    except ServiceUnavailableError:
        _log_trend_source_unavailable("approval_store")
    return metrics, events, approvals


async def _assemble_overview(  # noqa: PLR0913
    app_state: AppState,
    *,
    all_tasks: Sequence[Task],
    total_cost: float,
    agents: Sequence[AgentConfig],
    records_7d: Sequence[CostRecord],
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

    currency = await resolve_currency(config_resolver_of(app_state))
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
    metrics, lifecycle_events, approvals = await _collect_trend_sources(app_state, now)
    task_outcomes = await _resolve_task_outcomes(app_state, all_tasks)

    logger.debug(
        ANALYTICS_OVERVIEW_QUERIED,
        total_tasks=len(all_tasks),
        total_cost=total_cost,
        active_agents=active,
    )

    return OverviewMetrics(
        total_tasks=len(all_tasks),
        tasks_by_status=by_status,
        task_outcomes=task_outcomes,
        total_agents=len(agents),
        total_cost=total_cost,
        budget_remaining=budget.remaining,
        budget_used_percent=budget.used_percent,
        cost_7d_trend=cost_7d,
        tasks_7d_trend=tasks_completed_per_day(metrics, now),
        agents_7d_trend=roster_size_per_day(len(agents), lifecycle_events, now),
        review_7d_trend=approvals_raised_per_day(approvals, now),
        active_agents_count=active,
        idle_agents_count=idle,
        currency=currency,
    )


class AnalyticsOverviewController(Controller):
    """High-level analytics overview."""

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

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)

        try:
            async with asyncio.TaskGroup() as tg:
                t_tasks = tg.create_task(
                    analytics_read_service_of(app_state).list_tasks(),
                )
                t_cost = tg.create_task(
                    require_service(
                        app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
                    ).get_total_cost(),
                )
                t_agents = tg.create_task(
                    config_resolver_of(app_state).get_agents(),
                )
                t_7d = tg.create_task(
                    collect_all_records(
                        require_service(
                            app_state.slice(BudgetStateSlice).cost_tracker,
                            "Cost Tracker",
                        ),
                        start=now - timedelta(days=7),
                        end=now,
                    ),
                )
        except ExceptionGroup as eg:
            for inner in eg.exceptions:
                reraise_critical(inner)
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
