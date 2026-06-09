# module-kind: controller
"""Department health aggregation controller."""

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
from synthorg.api.state import AppState
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.normalization import find_by_name_ci
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_DEPARTMENT_HEALTH_QUERIED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class DepartmentHealthController(Controller):
    """Department health aggregation."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/{name:str}/health")
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
        budget_cfg = await config_resolver_of(app_state).get_budget_config()
        health = await assemble_department_health(
            app_state,
            canonical_name,
            dept_agents,
            currency=budget_cfg.currency,
        )

        logger.debug(
            API_DEPARTMENT_HEALTH_QUERIED,
            department=canonical_name,
            agent_count=health.agent_count,
            active_count=health.active_agent_count,
            cost_7d=health.department_cost_7d,
        )
        return ApiResponse(data=health)
