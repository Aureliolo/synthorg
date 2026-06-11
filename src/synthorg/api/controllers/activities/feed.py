# module-kind: controller
"""Org-wide activity feed controller."""

from datetime import timedelta
from enum import IntEnum
from typing import Annotated, Final

from litestar import Controller, Request, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.controllers.activities._shared import _build_timeline
from synthorg.api.dto import PaginatedResponse
from synthorg.api.guards import has_write_role, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.hr.activity import ActivityEvent, redact_cost_events
from synthorg.hr.enums import ActivityEventType
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_ACTIVITY_FEED_QUERIED
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class ActivityWindowHours(IntEnum):
    """Allowed time windows for the activity feed."""

    DAY = 24
    TWO_DAYS = 48
    WEEK = 168


class ActivityController(Controller):
    """Org-wide activity feed (REST fallback for WebSocket)."""

    path = "/activities"
    tags = ("activities",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_activities(  # noqa: PLR0913
        self,
        request: Request[object, object, State],
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        event_type: Annotated[
            ActivityEventType | None,
            QueryParameter(
                name="type",
                description="Filter by event_type",
            ),
        ] = None,
        agent_id: Annotated[
            str | None,
            QueryParameter(
                max_length=128,
                description="Filter by agent_id",
            ),
        ] = None,
        last_n_hours: Annotated[
            ActivityWindowHours,
            QueryParameter(description="Time window (24, 48, or 168 hours)"),
        ] = ActivityWindowHours.DAY,
    ) -> PaginatedResponse[ActivityEvent]:
        """Return a paginated org-wide activity feed.

        Merges lifecycle events, task metrics, cost records, tool
        invocations, and delegation records into a unified
        chronological timeline, most recent first.  Non-lifecycle
        data sources degrade gracefully when unavailable.

        Args:
            request: Incoming HTTP request (used for role-based redaction).
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            event_type: Filter by ``ActivityEventType`` (e.g. ``"hired"``).
                Invalid values are rejected with 400.
            agent_id: Filter events for a specific agent.
            last_n_hours: Time window in hours (24, 48, or 168).

        Returns:
            Paginated activity events.  The ``degraded_sources`` field
            lists any data sources that failed gracefully.
        """
        app_state: AppState = state.app_state
        now = app_state.clock.now()
        since = now - timedelta(hours=last_n_hours)
        lifecycle_cap = app_state.bridge_config.api.max_lifecycle_events_per_query

        lifecycle_events = await persistence_of(app_state).lifecycle_events.list_events(
            agent_id=agent_id,
            since=since,
            limit=lifecycle_cap,
        )

        timeline, degraded = await _build_timeline(
            app_state,
            lifecycle_events,
            agent_id,
            since,
            now,
        )

        if event_type is not None:
            timeline = tuple(e for e in timeline if e.event_type == event_type)

        # Redact cost details unless the user has a write role.
        # Fail-closed: redact by default if auth identity is missing
        # (e.g. misconfigured excluded path, test stub without scope["user"]).
        auth_user = request.scope.get("user")
        if not (
            isinstance(auth_user, AuthenticatedUser) and has_write_role(auth_user.role)
        ):
            timeline = redact_cost_events(timeline)

        page, meta = paginate_cursor(
            timeline,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )

        logger.debug(
            API_ACTIVITY_FEED_QUERIED,
            returned_events=len(page),
            has_more=meta.has_more,
            type_filter=event_type,
            agent_id_filter=agent_id,
            last_n_hours=last_n_hours,
        )

        return PaginatedResponse(
            data=page,
            pagination=meta,
            degraded_sources=tuple(degraded),
        )
