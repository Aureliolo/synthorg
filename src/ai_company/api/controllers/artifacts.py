"""Artifact controller (stub — no ArtifactRepository yet)."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.pagination import paginate


class ArtifactController(Controller):
    """Stub controller for artifacts.

    Full implementation will be added when artifact persistence
    is available.
    """

    path = "/artifacts"
    tags = ("artifacts",)

    @get()
    async def list_artifacts(
        self,
        state: State,  # noqa: ARG002
        offset: int = 0,
        limit: int = 50,
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
    ) -> ApiResponse[None]:
        """Get an artifact by ID (stub → not implemented).

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            Not implemented response.
        """
        return ApiResponse(
            success=False,
            error="Artifact persistence not implemented yet",
        )
