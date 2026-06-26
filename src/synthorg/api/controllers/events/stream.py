# module-kind: controller
"""AG-UI SSE event stream controller at /events.

Provides SSE event streaming at ``/events/stream``. The streaming
generator and its auth-revalidation machinery live in ``_sse``.
Interrupt resume is owned exclusively by ``InterruptController``
(``POST /interrupts/{id}/resume``); the SSE controller no longer
duplicates that path.
"""

from typing import Annotated

from litestar import Controller, Request, get
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException, ServiceUnavailableException
from litestar.params import QueryParameter
from litestar.response import ServerSentEvent

from synthorg.api.channels import get_channels_plugin
from synthorg.api.controllers.events._dashboard import (
    dashboard_channel_frames,
    resolve_dashboard_channels,
)
from synthorg.api.controllers.events._hub_access import require_hub
from synthorg.api.controllers.events._shared import (
    _SESSION_ID_PATTERN,
)
from synthorg.api.controllers.events._sse import (
    _sse_event_stream,
    assert_sse_session_access,
    revalidated_sse_stream,
)
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import QUERY_MAX_LENGTH
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SSE_INVALID_LAST_EVENT_ID

logger = get_logger(__name__)

# Event ids are UUID-shaped; cap the reconnect header so a crafted
# oversized ``Last-Event-ID`` cannot drive repeated linear scans over the
# per-session replay buffer.
_MAX_LAST_EVENT_ID_LENGTH: int = 64


class EventStreamController(Controller):
    """AG-UI SSE event stream."""

    path = "/events"
    tags = ("events",)

    @get(
        "/stream",
        media_type="text/event-stream",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("events.stream", key="user_or_ip"),
        ],
        opt=per_op_concurrency_from_policy(
            "events.stream",
            key="user",
        ),
    )
    async def stream(
        self,
        state: State,
        request: Request[object, object, State],
        session_id: Annotated[
            NotBlankStr,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                pattern=_SESSION_ID_PATTERN,
                description="Session ID whose AG-UI stream to subscribe to.",
            ),
        ],
    ) -> ServerSentEvent:
        """SSE stream of AG-UI events for a session.

        Args:
            state: Application state.
            request: Incoming HTTP request (for authenticated user).
            session_id: Session to subscribe to.

        Returns:
            SSE stream of projected events.

        Raises:
            NotFoundError: When the caller is neither the session-owning
                task's requester nor a CEO (404, never 403, so session
                ids cannot be enumerated by status code).
        """
        app_state: AppState = state.app_state
        hub = require_hub(app_state)
        user = getattr(request, "user", None)
        # ``require_read_access`` guarantees an authenticated user; assert it
        # so a misconfigured guard chain fails closed rather than streaming
        # a session to an anonymous caller.
        if not isinstance(user, AuthenticatedUser):
            msg = "Session not found"
            raise NotFoundError(msg)
        await assert_sse_session_access(app_state, session_id, user)
        # SSE reconnect: the browser resends the last event id it saw via
        # the ``Last-Event-ID`` header so the hub can replay the gap it
        # missed while disconnected.
        after_id = request.headers.get("last-event-id") or None
        if after_id is not None and len(after_id) > _MAX_LAST_EVENT_ID_LENGTH:
            # Oversized header: a real event id is UUID-shaped, so drop the
            # crafted value and fall back to a normal (no-replay) subscribe.
            logger.warning(
                API_SSE_INVALID_LAST_EVENT_ID,
                session_id=session_id,
                length=len(after_id),
            )
            after_id = None
        return ServerSentEvent(
            content=_sse_event_stream(
                hub,
                session_id,
                app_state=app_state,
                user=user,
                after_id=after_id,
            ),
        )

    @get(
        "/dashboard",
        media_type="text/event-stream",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("events.stream", key="user_or_ip"),
        ],
        opt=per_op_concurrency_from_policy(
            "events.stream",
            key="user",
        ),
    )
    async def dashboard_stream(
        self,
        state: State,
        request: Request[object, object, State],
        last_event_id: Annotated[
            str | None,
            QueryParameter(
                required=False,
                max_length=_MAX_LAST_EVENT_ID_LENGTH,
                description="Reconnect cursor; presence replays the recent backlog.",
            ),
        ] = None,
    ) -> ServerSentEvent:
        """Session-less SSE feed of the user's dashboard channels.

        The read-only fallback the SPA opens when the WebSocket upgrade is
        proxy-blocked. Subscribes to every channel the caller may read (plus
        their user channel), forwards each ``WsEvent`` as a ``ws`` frame, and
        replays the recent backlog when ``last_event_id`` is present.

        Args:
            state: Application state.
            request: Incoming HTTP request (for the authenticated user).
            last_event_id: Reconnect cursor; presence triggers backlog replay.

        Returns:
            SSE stream of the user's channel events.

        Raises:
            NotAuthorizedException: When no authenticated user is present.
            ServiceUnavailableException: When the channel feed is not wired.
        """
        app_state: AppState = state.app_state
        user = getattr(request, "user", None)
        if not isinstance(user, AuthenticatedUser):
            msg = "Authentication required"
            raise NotAuthorizedException(msg)
        plugin = get_channels_plugin(request)
        if plugin is None:
            msg = "Real-time channel feed unavailable"
            raise ServiceUnavailableException(msg)
        channels = resolve_dashboard_channels(user)
        inner = dashboard_channel_frames(
            plugin,
            channels,
            app_state=app_state,
            replay=last_event_id is not None,
        )
        return ServerSentEvent(
            content=revalidated_sse_stream(inner, app_state=app_state, user=user),
        )
