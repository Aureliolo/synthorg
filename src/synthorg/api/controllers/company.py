"""Company configuration controller."""

from typing import Any

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.company import Department  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)


class CompanyController(Controller):
    """Read-only access to company configuration."""

    path = "/company"
    tags = ("company",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def get_company(
        self,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Return a curated subset of company configuration.

        Returns an explicit field dict to control the response
        shape and avoid exposing internal configuration details.

        Args:
            state: Application state.

        Returns:
            Company configuration envelope.
        """
        app_state: AppState = state.app_state
        config = app_state.config
        data: dict[str, Any] = {
            "company_name": config.company_name,
            "agents": [a.model_dump(mode="json") for a in config.agents],
            "departments": [d.model_dump(mode="json") for d in config.departments],
        }
        return ApiResponse(data=data)

    @get("/departments")
    async def list_departments(
        self,
        state: State,
    ) -> ApiResponse[tuple[Department, ...]]:
        """List departments (convenience alias).

        Args:
            state: Application state.

        Returns:
            Departments envelope.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=app_state.config.departments)
