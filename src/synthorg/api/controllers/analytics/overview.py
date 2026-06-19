# module-kind: controller
"""Analytics overview endpoint at /analytics/overview."""

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import analytics_read_service_of
from synthorg.api.controllers.analytics._shared import (
    OverviewMetrics,
    _resolve_agent_counts,
    _resolve_budget_context,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.budget.trends import BucketSize, bucket_cost_records
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import ANALYTICS_OVERVIEW_QUERIED
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


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

    currency = await config_resolver_of(app_state).get_str("budget", "currency")
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
