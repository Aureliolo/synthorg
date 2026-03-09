"""Department controller."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.errors import NotFoundError
from ai_company.api.pagination import paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.core.company import Department  # noqa: TC001


class DepartmentController(Controller):
    """Read-only access to departments."""

    path = "/departments"
    tags = ("departments",)

    @get()
    async def list_departments(
        self,
        state: State,
        offset: int = 0,
        limit: int = 50,
    ) -> PaginatedResponse[Department]:
        """List all departments.

        Args:
            state: Application state.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated department list.
        """
        app_state: AppState = state.app_state
        page, meta = paginate(
            app_state.config.departments,
            offset=offset,
            limit=limit,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{name:str}")
    async def get_department(
        self,
        state: State,
        name: str,
    ) -> ApiResponse[Department]:
        """Get a department by name.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Department envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state
        for dept in app_state.config.departments:
            if dept.name == name:
                return ApiResponse(data=dept)
        msg = f"Department {name!r} not found"
        raise NotFoundError(msg)
