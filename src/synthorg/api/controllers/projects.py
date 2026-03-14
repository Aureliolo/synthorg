"""Project controller (stub — no ProjectRepository yet)."""

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ProjectController(Controller):
    """Stub controller for project management.

    Projects are not yet persisted — returns empty results.
    Full CRUD will be added when a ``ProjectRepository`` exists.
    """

    path = "/projects"
    tags = ("projects",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_projects(
        self,
        state: State,  # noqa: ARG002
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[object]:
        """List projects (empty — no repository yet).

        Args:
            state: Application state.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Empty paginated response.
        """
        empty: tuple[object, ...] = ()
        page, meta = paginate(empty, offset=offset, limit=limit)
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{project_id:str}")
    async def get_project(
        self,
        state: State,  # noqa: ARG002
        project_id: str,  # noqa: ARG002
    ) -> Response[ApiResponse[None]]:
        """Get a project by ID (stub → 501).

        Args:
            state: Application state.
            project_id: Project identifier.

        Returns:
            Not implemented response.
        """
        return Response(
            content=ApiResponse[None](
                error="Project persistence not implemented yet",
            ),
            status_code=501,
        )
