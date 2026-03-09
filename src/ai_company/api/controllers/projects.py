"""Project controller (stub — no ProjectRepository yet)."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.pagination import paginate


class ProjectController(Controller):
    """Stub controller for project management.

    Projects are not yet persisted — returns empty results.
    Full CRUD will be added when a ``ProjectRepository`` exists.
    """

    path = "/projects"
    tags = ("projects",)

    @get()
    async def list_projects(
        self,
        state: State,  # noqa: ARG002
        offset: int = 0,
        limit: int = 50,
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
    ) -> ApiResponse[None]:
        """Get a project by ID (stub → 501).

        Args:
            state: Application state.
            project_id: Project identifier.

        Returns:
            Not implemented response.
        """
        return ApiResponse(
            success=False,
            error="Project persistence not implemented yet",
        )
