"""Meeting controller — list, get, and trigger meetings."""

from typing import Self

from litestar import Controller, Response, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.communication.meeting.enums import MeetingStatus  # noqa: TC001
from synthorg.communication.meeting.models import MeetingRecord
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import MEETING_NOT_FOUND

logger = get_logger(__name__)

_MAX_CONTEXT_KEYS: int = 20
_MAX_CONTEXT_KEY_LEN: int = 256
_MAX_CONTEXT_VAL_LEN: int = 1024
_MAX_CONTEXT_LIST_ITEMS: int = 50


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
    context: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="Event context for participant resolution and agenda",
    )

    @model_validator(mode="after")
    def _validate_context_bounds(self) -> Self:
        """Limit context size to prevent abuse."""
        if len(self.context) > _MAX_CONTEXT_KEYS:
            msg = f"context must have at most {_MAX_CONTEXT_KEYS} keys"
            raise ValueError(msg)
        for k, v in self.context.items():
            if len(k) > _MAX_CONTEXT_KEY_LEN:
                msg = f"context key must be at most {_MAX_CONTEXT_KEY_LEN} characters"
                raise ValueError(msg)
            if isinstance(v, list):
                if len(v) > _MAX_CONTEXT_LIST_ITEMS:
                    msg = (
                        f"context list must have at most"
                        f" {_MAX_CONTEXT_LIST_ITEMS} items"
                    )
                    raise ValueError(msg)
                for item in v:
                    if len(item) > _MAX_CONTEXT_VAL_LEN:
                        msg = (
                            f"context list item must be at most"
                            f" {_MAX_CONTEXT_VAL_LEN} characters"
                        )
                        raise ValueError(msg)
            elif len(v) > _MAX_CONTEXT_VAL_LEN:
                msg = f"context value must be at most {_MAX_CONTEXT_VAL_LEN} characters"
                raise ValueError(msg)
        return self


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

        logger.warning(
            MEETING_NOT_FOUND,
            meeting_id=meeting_id,
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
            context=data.context,
        )

        return Response(
            content=ApiResponse[tuple[MeetingRecord, ...]](data=records),
            status_code=200,
        )
