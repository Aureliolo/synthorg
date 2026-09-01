# module-kind: controller
"""AG-UI SSE event stream controller at /events.

Provides SSE event streaming at ``/events/stream``. The streaming
generator and its auth-revalidation machinery live in ``_sse``.
Interrupt resume is owned exclusively by ``InterruptController``
(``POST /interrupts/{id}/resume``); this controller streams only.
"""

from typing import Annotated

from litestar import Controller, Request, get
from litestar.channels import ChannelsPlugin
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
from synthorg.observability.events.api import (
    API_DASHBOARD_SSE_FEED_UNAVAILABLE,
    API_SSE_INVALID_LAST_EVENT_ID,
)

logger = get_logger(__name__)

# Event ids are UUID-shaped; cap the reconnect header so a crafted
# oversized ``Last-Event-ID`` cannot drive repeated linear scans over the
# per-session replay buffer.
_MAX_LAST_EVENT_ID_LENGTH: int = 64


def _clamp_after_id(raw: str | None, *, session_id: str) -> str | None:
    """Drop an oversized ``Last-Event-ID`` reconnect value.

    A real event id is UUID-shaped, so an over-length value is crafted and
    dropping it falls back to a normal (no-replay) subscribe rather than
    driving repeated linear scans over the per-session replay buffer.

    Returns:
        The reconnect id, or ``None`` when absent or over the length cap.
    """
    if raw is not None and len(raw) > _MAX_LAST_EVENT_ID_LENGTH:
        logger.warning(
            API_SSE_INVALID_LAST_EVENT_ID, session_id=session_id, length=len(raw)
        )
        return None
    return raw


def _require_dashboard_feed(
    request: Request[object, object, State],
) -> tuple[ChannelsPlugin, AuthenticatedUser]:
    """Resolve the authenticated user and channel plugin for the dashboard feed.

    Returns:
        The channel plugin and the authenticated user.

    Raises:
        NotAuthorizedException: When no authenticated user is present (the
            guard chain should already have blocked this).
        ServiceUnavailableException: When the channel feed is not wired.
    """
    user = getattr(request, "user", None)
    if not isinstance(user, AuthenticatedUser):
        # The guard chain (require_read_access) should already have blocked
        # this; raise the client-facing auth exception directly rather than
        # logging a WARNING on a routine auth failure (attacker-controllable
        # log noise). The unavailable-feed branch below keeps its WARNING.
        #
        # The detail string is one auth_response_discriminator recognises.
        # Any other wording falls through to its unknown-detail arm, which
        # logs a WARNING announcing that producer and consumer have drifted,
        # so an unregistered producer here reports a divergence that has not
        # happened.
        msg = "Missing authentication"
        raise NotAuthorizedException(msg)
    plugin = get_channels_plugin(request)
    if plugin is None:
        logger.warning(API_DASHBOARD_SSE_FEED_UNAVAILABLE, user_id=user.user_id)
        msg = "Real-time channel feed unavailable"
        raise ServiceUnavailableException(msg)
    return plugin, user


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
        user = getattr(request, "user", None)
        # ``require_read_access`` guarantees an authenticated user; assert it
        # FIRST so a misconfigured guard chain fails closed with the generic
        # 404 rather than streaming a session to an anonymous caller or
        # leaking the hub-availability 503 ahead of the access check.
        if not isinstance(user, AuthenticatedUser):
            msg = "Session not found"
            raise NotFoundError(msg)
        hub = require_hub(app_state)
        await assert_sse_session_access(app_state, session_id, user)
        # SSE reconnect: the browser resends the last event id it saw via
        # the ``Last-Event-ID`` header so the hub can replay the gap it
        # missed while disconnected.
        after_id = _clamp_after_id(
            request.headers.get("last-event-id") or None, session_id=session_id
        )
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
        plugin, user = _require_dashboard_feed(request)
        channels = resolve_dashboard_channels(user)
        # A reconnect (``last_event_id`` present) replays the recent backlog for
        # gap recovery. The cursor's value is not used to slice the backlog
        # server-side: each ``WsEvent`` carries a stable ``event_id`` and the
        # client deduplicates by it, so a replayed event is dispatched exactly
        # once without the server tracking per-connection cursor state.
        inner = dashboard_channel_frames(
            plugin,
            channels,
            app_state=app_state,
            replay=last_event_id is not None,
        )
        return ServerSentEvent(
            content=revalidated_sse_stream(inner, app_state=app_state, user=user),
        )
