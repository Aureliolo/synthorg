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
from litestar.params import QueryParameter
from litestar.response import ServerSentEvent

from synthorg.api.controllers.events._hub_access import require_hub
from synthorg.api.controllers.events._shared import (
    _SESSION_ID_PATTERN,
)
from synthorg.api.controllers.events._sse import (
    _sse_event_stream,
    assert_sse_session_access,
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
