"""Meeting controller -- list, get, and trigger meetings."""

import asyncio
from typing import Annotated, Any, Final, Self

from litestar import Controller, Request, delete, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.communication.meeting.enums import MeetingStatus
from synthorg.communication.meeting.models import MeetingRecord
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_MEETING_TRIGGERED,
    API_SETTINGS_BACKEND_RECOVERED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.meeting import MEETING_NOT_FOUND
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

# Fallback used only when the settings backend is unavailable; the
# authoritative cap is ``api.max_meeting_context_keys``, resolved
# per-request in ``trigger_meeting``.
_MAX_CONTEXT_KEYS_FALLBACK: int = 20
_MAX_CONTEXT_KEY_LEN: int = 256
_MAX_CONTEXT_VAL_LEN: int = 1024
_MAX_CONTEXT_LIST_ITEMS: int = 50


_meeting_context_cap_fallback_logged: bool = False


async def _resolve_max_context_keys(app_state: AppState) -> int:
    """Read the ``api.max_meeting_context_keys`` setting at request time.

    Falls back to ``_MAX_CONTEXT_KEYS_FALLBACK`` (20) when the settings
    backend is unavailable.  Per-process log-once so a flapping settings
    backend does not spam the logs.

    Returns:
        Resulting integer.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    global _meeting_context_cap_fallback_logged  # noqa: PLW0603
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        # Treat absent resolver as a fallback path identical to the
        # except-branch below: log once on first observation so
        # operators see the gap, and arm the recovery log so it fires
        # the moment a resolver becomes available.
        if not _meeting_context_cap_fallback_logged:
            logger.warning(
                API_VALIDATION_FAILED,
                error=(
                    "no config resolver available;"
                    " using max_meeting_context_keys fallback"
                ),
                cap=_MAX_CONTEXT_KEYS_FALLBACK,
            )
            _meeting_context_cap_fallback_logged = True
        return _MAX_CONTEXT_KEYS_FALLBACK
    try:
        result: int = await config_resolver_of(app_state).get_int(
            SettingNamespace.API.value, "max_meeting_context_keys"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        if not _meeting_context_cap_fallback_logged:
            logger.warning(
                API_VALIDATION_FAILED,
                error=(
                    "failed to resolve max_meeting_context_keys;"
                    f" using fallback ({type(exc).__name__})"
                ),
                cap=_MAX_CONTEXT_KEYS_FALLBACK,
            )
            _meeting_context_cap_fallback_logged = True
        return _MAX_CONTEXT_KEYS_FALLBACK
    if result < 0:
        # Negative caps are nonsensical: ``len(context) > cap`` would
        # always be ``True`` and reject every payload.  Treat as a
        # backend misconfiguration and fall back so operators can
        # recover by fixing the setting.
        if not _meeting_context_cap_fallback_logged:
            logger.warning(
                API_VALIDATION_FAILED,
                error=(
                    "max_meeting_context_keys resolved to a negative value;"
                    " using fallback"
                ),
                resolved=result,
                cap=_MAX_CONTEXT_KEYS_FALLBACK,
            )
            _meeting_context_cap_fallback_logged = True
        return _MAX_CONTEXT_KEYS_FALLBACK
    if _meeting_context_cap_fallback_logged:
        logger.info(
            API_SETTINGS_BACKEND_RECOVERED,
            setting="max_meeting_context_keys",
            cap=result,
        )
        _meeting_context_cap_fallback_logged = False
    return result


class TriggerMeetingRequest(BaseModel):
    """Request body for triggering an event-based meeting.

    Attributes:
        event_name: Event trigger name to match against meeting configs.
        context: Optional context passed to participant resolver and agenda.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_name: NotBlankStr = Field(
        description="Event trigger name",
    )
    context: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="Event context for participant resolution and agenda",
    )

    @model_validator(mode="after")
    def _validate_context_bounds(self) -> Self:
        """Validate per-key and per-value lengths.

        The aggregate ``len(context) <= max_meeting_context_keys`` cap
        is enforced controller-side in ``trigger_meeting`` so operators
        can tune it via the ``api.max_meeting_context_keys`` setting
        without code changes.  This validator only checks per-key /
        per-value invariants which are static.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
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


class MeetingResponse(MeetingRecord):
    """Meeting record enriched with per-participant analytics.

    Attributes:
        token_usage_by_participant: Total tokens per agent.
        contribution_rank: Agent IDs sorted by total tokens (desc).
        meeting_duration_seconds: Duration in seconds (populated when
            minutes are present, ``None`` otherwise).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    token_usage_by_participant: dict[str, int] = Field(
        default_factory=dict,
        description="Total tokens consumed per participant",
    )
    contribution_rank: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs sorted by contribution (descending)",
    )
    meeting_duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Meeting duration in seconds (null if no minutes)",
    )


def _to_meeting_response(record: MeetingRecord) -> MeetingResponse:
    """Convert a MeetingRecord to a MeetingResponse with analytics.

    Args:
        record: The domain-layer meeting record.

    Returns:
        Response DTO with per-participant token usage (sum of input +
        output tokens across all contributions), contribution ranking
        by total tokens descending, and duration (when minutes are
        present).
    """
    usage: dict[str, int] = {}
    rank: tuple[str, ...] = ()
    duration: float | None = None

    if record.minutes is not None:
        for c in record.minutes.contributions:
            usage[c.agent_id] = (
                usage.get(c.agent_id, 0) + c.input_tokens + c.output_tokens
            )
        rank = tuple(
            sorted(usage, key=usage.__getitem__, reverse=True),
        )
        delta = record.minutes.ended_at - record.minutes.started_at
        duration = max(0.0, delta.total_seconds())

    return MeetingResponse(
        **record.model_dump(),
        token_usage_by_participant=usage,
        contribution_rank=rank,
        meeting_duration_seconds=duration,
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
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        status: Annotated[
            MeetingStatus | None,
            QueryParameter(description="Filter to meetings in this status."),
        ] = None,
        meeting_type: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter by meeting type (STAND_UP, RETRO, etc.).",
            ),
        ] = None,
    ) -> PaginatedResponse[MeetingResponse]:
        """List meeting records with optional filters.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            status: Optional status filter.
            meeting_type: Optional meeting type name filter.

        Returns:
            Paginated meeting records with analytics fields.
        """
        orchestrator = require_service(
            state.app_state.slice(CommunicationStateSlice).meeting_orchestrator,
            "Meeting Orchestrator",
        )
        records = orchestrator.get_records()

        if status is not None:
            records = tuple(r for r in records if r.status == status)
        if meeting_type is not None:
            records = tuple(r for r in records if r.meeting_type_name == meeting_type)

        page, meta = paginate_cursor(
            records,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        enriched = tuple(_to_meeting_response(r) for r in page)
        return PaginatedResponse(data=enriched, pagination=meta)

    @get("/{meeting_id:str}")
    async def get_meeting(
        self,
        state: State,
        meeting_id: PathId,
    ) -> ApiResponse[MeetingResponse]:
        """Get a meeting record by ID.

        Args:
            state: Application state.
            meeting_id: Meeting identifier.

        Returns:
            Meeting response envelope with analytics fields.

        Raises:
            NotFoundError: If the meeting is not found.
        """
        orchestrator = require_service(
            state.app_state.slice(CommunicationStateSlice).meeting_orchestrator,
            "Meeting Orchestrator",
        )
        record = orchestrator.get_record(meeting_id)
        if record is not None:
            return ApiResponse(data=_to_meeting_response(record))

        logger.warning(
            MEETING_NOT_FOUND,
            meeting_id=meeting_id,
        )
        msg = f"Meeting {meeting_id!r} not found"
        raise NotFoundError(msg)

    @delete(
        "/{meeting_id:str}",
        status_code=200,
        guards=[require_write_access],
    )
    async def delete_meeting(
        self,
        state: State,
        request: Request[Any, Any, Any],
        meeting_id: PathId,
    ) -> ApiResponse[None]:
        """Delete a meeting record by id.

        Routes through :class:`MeetingService` so the
        ``COMMUNICATION_MEETING_DELETED`` audit event is emitted from
        the service layer on parity with the MCP path.

        Args:
            state: Litestar app state container.
            request: Authenticated request; ``request.user.user_id``
                drives the audit log's actor field.
            meeting_id: Meeting identifier (matches
                ``MeetingRecord.meeting_id``).

        Returns:
            ``ApiResponse[None]`` with ``data=None`` on success.

        Raises:
            NotFoundError: If no meeting exists for ``meeting_id``.
        """
        app_state: AppState = state.app_state
        meeting_service = require_service(
            app_state.slice(CommunicationStateSlice).meeting_service,
            "Meeting Service",
        )
        deleted = await meeting_service.delete_meeting(
            meeting_id=NotBlankStr(meeting_id),
            actor_id=NotBlankStr(str(request.user.user_id)),
            reason=NotBlankStr("operator delete via REST API"),
        )
        if not deleted:
            logger.warning(MEETING_NOT_FOUND, meeting_id=meeting_id)
            msg = f"Meeting {meeting_id!r} not found"
            raise NotFoundError(msg)
        return ApiResponse(data=None)

    @post(
        "/trigger",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("meetings.create", key="user"),
        ],
        status_code=200,
    )
    async def trigger_meeting(
        self,
        state: State,
        data: TriggerMeetingRequest,
    ) -> ApiResponse[tuple[MeetingResponse, ...]]:
        """Trigger event-based meetings by event name.

        Args:
            state: Application state.
            data: Trigger request with event name and context.

        Returns:
            Tuple of meeting responses for all triggered meetings.

        Raises:
            ValidationError: If ``data.context`` exceeds the
                operator-configured key cap
                (``api.max_meeting_context_keys``).
            ServiceUnavailableError: Raised (503) when the meeting
                scheduler was not auto-wired -- happens in the degraded
                (unconfigured) meeting agent caller mode.  The operator
                must provide the agent and provider registries before
                meetings can be triggered.
        """
        app_state: AppState = state.app_state
        # Resolve the scheduler FIRST: a ``None`` scheduler (degraded
        # mode) surfaces a clean 503 here. Failing fast before the
        # settings-backend round trip avoids unnecessary I/O when the
        # endpoint can't dispatch anyway.
        scheduler = require_service(
            app_state.slice(CommunicationStateSlice).meeting_scheduler,
            "Meeting Scheduler",
        )
        max_keys = await _resolve_max_context_keys(app_state)
        if len(data.context) > max_keys:
            msg = f"context must have at most {max_keys} keys"
            logger.warning(
                API_VALIDATION_FAILED,
                field="context",
                actual_keys=len(data.context),
                max_keys=max_keys,
            )
            raise ValidationError(msg)
        records = await scheduler.trigger_event(
            data.event_name,
            context=data.context,
        )
        enriched = tuple(_to_meeting_response(r) for r in records)
        logger.info(
            API_MEETING_TRIGGERED,
            event_name=data.event_name,
            meetings_triggered=len(records),
        )
        return ApiResponse(data=enriched)
