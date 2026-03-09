"""Meeting controller (stub — no MeetingRepository yet)."""

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.guards import require_read_access
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate


class MeetingController(Controller):
    """Stub controller for meetings.

    Full implementation will be added when meeting persistence
    is available.
    """

    path = "/meetings"
    tags = ("meetings",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_meetings(
        self,
        state: State,  # noqa: ARG002
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[object]:
        """List meetings (empty — no repository yet).

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

    @get("/{meeting_id:str}")
    async def get_meeting(
        self,
        state: State,  # noqa: ARG002
        meeting_id: str,  # noqa: ARG002
    ) -> Response[ApiResponse[None]]:
        """Get a meeting by ID (stub → not implemented).

        Args:
            state: Application state.
            meeting_id: Meeting identifier.

        Returns:
            Not implemented response.
        """
        return Response(
            content=ApiResponse[None](
                success=False,
                error="Meeting persistence not implemented yet",
            ),
            status_code=501,
        )
