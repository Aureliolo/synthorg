"""Artifact controller (stub — no ArtifactRepository yet)."""

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ArtifactController(Controller):
    """Stub controller for artifacts.

    Full implementation will be added when artifact persistence
    is available.
    """

    path = "/artifacts"
    tags = ("artifacts",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_artifacts(
        self,
        state: State,  # noqa: ARG002
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[object]:
        """List artifacts (empty — no repository yet).

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

    @get("/{artifact_id:str}")
    async def get_artifact(
        self,
        state: State,  # noqa: ARG002
        artifact_id: str,  # noqa: ARG002
    ) -> Response[ApiResponse[None]]:
        """Get an artifact by ID (stub → not implemented).

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            Not implemented response.
        """
        return Response(
            content=ApiResponse[None](
                error="Artifact persistence not implemented yet",
            ),
            status_code=501,
        )
