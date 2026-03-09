"""Company configuration controller."""

from typing import Any

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.core.company import Department  # noqa: TC001
from ai_company.observability import get_logger

logger = get_logger(__name__)


class CompanyController(Controller):
    """Read-only access to company configuration."""

    path = "/company"
    tags = ("company",)

    @get()
    async def get_company(
        self,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Return the current company configuration.

        Returns an explicit field dict because ``RootConfig`` contains
        ``MappingProxyType`` fields that Pydantic/Litestar cannot
        serialise directly.

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
