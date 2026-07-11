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
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.normalization import find_by_name_ci
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_DEPARTMENT_HEALTH_QUERIED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class DepartmentHealthController(Controller):
    """Department health aggregation."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

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

        Aggregates agent count, utilization, cost, performance, and
        collaboration data for the named department.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Department health envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state

        # Fetch departments and agents (both are config reads)
        departments = await config_resolver_of(app_state).get_departments()
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

        agents = await config_resolver_of(app_state).get_agents()
        dept_agents = filter_agents_by_department(agents, canonical_name)
        resolver = config_resolver_of(app_state)
        # The budget config and the two health settings are independent reads;
        # resolve them concurrently rather than serialising three round-trips.
        # A failed read escapes TaskGroup as an ExceptionGroup, which would skip
        # the controller's typed exception handlers -- unwrap and re-raise the
        # leaf so the failure is handled as if the reads ran sequentially.
        try:
            async with asyncio.TaskGroup() as tg:
                budget_task = tg.create_task(resolver.get_budget_config())
                window_task = tg.create_task(
                    resolver.get_int(
                        SettingNamespace.HR, "department_health_window_days"
                    )
                )
                min_runs_task = tg.create_task(
                    resolver.get_int(SettingNamespace.HR, "department_health_min_runs")
                )
        except BaseExceptionGroup as eg:
            raise eg.exceptions[0] from None
        budget_cfg = budget_task.result()
        window_days = window_task.result()
        min_runs = min_runs_task.result()
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
