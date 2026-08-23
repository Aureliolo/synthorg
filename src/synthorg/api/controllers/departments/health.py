# module-kind: controller
"""Department health aggregation controller."""

import asyncio

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.controllers._department_health import (
    DepartmentHealth,
    assemble_department_health,
    filter_agents_by_department,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.config import BudgetConfig
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.normalization import find_by_name_ci
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_DEPARTMENT_HEALTH_QUERIED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class DepartmentHealthController(Controller):
    """Department health aggregation."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get(
        "/health",
        guards=[per_op_rate_limit_from_policy("departments.health_all")],
    )
    async def list_department_health(
        self,
        state: State,
    ) -> ApiResponse[tuple[DepartmentHealth, ...]]:
        """Report every department's health in one read.

        The org-health panel wants all of them at once, and asking per
        department cost one request per row: six on a small org, against a
        per-operation budget of thirty a minute, so five dashboard views in a
        minute exhausted it and the panel rendered the refusals as "no
        departments configured".

        The per-department route stays: a department page asks about one.

        Bucketed on its own operation id: a grant here costs one assembly
        per department, so sharing the fixed-unit route's budget would let a
        read-access caller drive that budget times the roster size.

        Returns:
            One health aggregation per department, in roster order.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)
        departments = await resolver.get_departments()
        agents = await resolver.get_agents()
        budget_cfg, window_days, min_runs = await _resolve_health_settings(resolver)
        # Sequential on purpose: each assembly reads the same stores, and a
        # fan-out over every department would multiply the concurrent load
        # this route exists to reduce.
        healths = [
            await assemble_department_health(
                app_state,
                dept.name,
                filter_agents_by_department(agents, dept.name),
                currency=budget_cfg.currency,
                health_window_days=window_days,
                health_min_runs=min_runs,
            )
            for dept in departments
        ]
        logger.debug(
            API_DEPARTMENT_HEALTH_QUERIED,
            department="*",
            department_count=len(healths),
        )
        return ApiResponse(data=tuple(healths))

    @get(
        "/{name:str}/health",
        guards=[per_op_rate_limit_from_policy("departments.health")],
    )
    async def get_department_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[DepartmentHealth]:
        """Get department health aggregation.

        Aggregates agent count, utilization, cost and performance data
        for the named department.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Department health envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)

        departments = await resolver.get_departments()
        dept = find_by_name_ci(departments, name)
        if dept is None:
            msg = f"Department {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="department",
                name=name,
            )
            raise NotFoundError(msg)
        canonical_name = dept.name

        agents = await resolver.get_agents()
        dept_agents = filter_agents_by_department(agents, canonical_name)
        budget_cfg, window_days, min_runs = await _resolve_health_settings(resolver)
        health = await assemble_department_health(
            app_state,
            canonical_name,
            dept_agents,
            currency=budget_cfg.currency,
            health_window_days=window_days,
            health_min_runs=min_runs,
        )

        logger.debug(
            API_DEPARTMENT_HEALTH_QUERIED,
            department=canonical_name,
            agent_count=health.agent_count,
            active_count=health.active_agent_count,
            cost_7d=health.department_cost_7d,
        )
        return ApiResponse(data=health)


async def _resolve_health_settings(
    resolver: ConfigResolver,
) -> tuple[BudgetConfig, int, int]:
    """Resolve budget config and the health-window/min-runs settings concurrently.

    The three reads are independent, so they run in one TaskGroup rather than
    three serial round-trips.

    Args:
        resolver: Wired config resolver.

    Returns:
        The budget config, health window in days, and minimum runs.

    Raises:
        BaseException: The first failing read, unwrapped from the TaskGroup's
            ExceptionGroup (with the group preserved as the cause) so the
            controller's typed exception handlers see the leaf.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            budget_task = tg.create_task(resolver.get_budget_config())
            window_task = tg.create_task(
                resolver.get_int(SettingNamespace.HR, "department_health_window_days")
            )
            min_runs_task = tg.create_task(
                resolver.get_int(SettingNamespace.HR, "department_health_min_runs")
            )
    except BaseExceptionGroup as eg:
        raise eg.exceptions[0] from eg
    return budget_task.result(), window_task.result(), min_runs_task.result()
