"""Department controller."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.errors import NotFoundError
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.company import Department  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND

logger = get_logger(__name__)


class DepartmentController(Controller):
    """Read-only access to departments."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_departments(
        self,
        state: State,
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
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
        logger.warning(API_RESOURCE_NOT_FOUND, resource="department", name=name)
        raise NotFoundError(msg)
