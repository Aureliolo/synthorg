"""Meeting controller — list, get, and trigger meetings."""

from typing import Any

from litestar import Controller, Response, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.guards import require_read_access, require_write_access
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate
from ai_company.communication.meeting.enums import MeetingStatus  # noqa: TC001
from ai_company.communication.meeting.models import MeetingRecord
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger

logger = get_logger(__name__)


class TriggerMeetingRequest(BaseModel):
    """Request body for triggering an event-based meeting.

    Attributes:
        event_name: Event trigger name to match against meeting configs.
        context: Optional context passed to participant resolver and agenda.
    """

    model_config = ConfigDict(frozen=True)

    event_name: NotBlankStr = Field(
        description="Event trigger name",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Event context for participant resolution and agenda",
    )


class MeetingController(Controller):
    """Meetings resource controller.

    Provides endpoints for listing, getting, and triggering meetings.
    """

    path = "/meetings"
    tags = ("meetings",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_meetings(
        self,
        state: State,
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
        status: MeetingStatus | None = None,
        meeting_type: str | None = None,
    ) -> PaginatedResponse[MeetingRecord]:
        """List meeting records with optional filters.

        Args:
            state: Application state.
            offset: Pagination offset.
            limit: Page size.
            status: Optional status filter.
            meeting_type: Optional meeting type name filter.

        Returns:
            Paginated meeting records.
        """
        orchestrator = state.app_state.meeting_orchestrator
        records = orchestrator.get_records()

        if status is not None:
            records = tuple(r for r in records if r.status == status)
        if meeting_type is not None:
            records = tuple(r for r in records if r.meeting_type_name == meeting_type)

        page, meta = paginate(records, offset=offset, limit=limit)
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{meeting_id:str}")
    async def get_meeting(
        self,
        state: State,
        meeting_id: str,
    ) -> Response[ApiResponse[MeetingRecord]]:
        """Get a meeting record by ID.

        Args:
            state: Application state.
            meeting_id: Meeting identifier.

        Returns:
            Meeting record or 404.
        """
        orchestrator = state.app_state.meeting_orchestrator
        records = orchestrator.get_records()

        for record in records:
            if record.meeting_id == meeting_id:
                return Response(
                    content=ApiResponse[MeetingRecord](data=record),
                    status_code=200,
                )

        return Response(
            content=ApiResponse[MeetingRecord](
                error=f"Meeting {meeting_id!r} not found",
            ),
            status_code=404,
        )

    @post(
        "/trigger",
        guards=[require_write_access],
    )
    async def trigger_meeting(
        self,
        state: State,
        data: TriggerMeetingRequest,
    ) -> Response[ApiResponse[tuple[MeetingRecord, ...]]]:
        """Trigger event-based meetings by event name.

        Args:
            state: Application state.
            data: Trigger request with event name and context.

        Returns:
            Tuple of meeting records for all triggered meetings.
        """
        scheduler = state.app_state.meeting_scheduler
        records = await scheduler.trigger_event(
            data.event_name,
            context=data.context or None,
        )

        return Response(
            content=ApiResponse[tuple[MeetingRecord, ...]](data=records),
            status_code=200,
        )
