# module-kind: controller
"""AG-UI SSE event stream controller at /events.

Provides SSE event streaming at ``/events/stream`` plus the SSE-side
interrupt resume endpoint. The streaming generator and its
auth-revalidation machinery live in ``_sse``; the resume DTOs and the
shared resume body live in ``_shared``.
"""

from typing import Annotated

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.response import ServerSentEvent

from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.controllers.events._shared import (
    _SESSION_ID_PATTERN,
    ResumeInterruptRequest,
    _require_interrupt_store,
    _resolve_interrupt,
)
from synthorg.api.controllers.events._sse import (
    _require_hub,
    _sse_event_stream,
    assert_sse_session_access,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_approval_roles, require_read_access
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr


class EventStreamController(Controller):
    """AG-UI SSE event stream and interrupt resume."""

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
        hub = _require_hub(app_state)
        user = getattr(request, "user", None)
        # ``require_read_access`` guarantees an authenticated user; assert it
        # so a misconfigured guard chain fails closed rather than streaming
        # a session to an anonymous caller.
        if not isinstance(user, AuthenticatedUser):
            msg = "Session not found"
            raise NotFoundError(msg)
        await assert_sse_session_access(app_state, session_id, user)
        return ServerSentEvent(
            content=_sse_event_stream(
                hub,
                session_id,
                app_state=app_state,
                user=user,
            ),
        )

    @post(
        "/resume/{interrupt_id:str}",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("interrupts.resume", key="user"),
        ],
        status_code=200,
    )
    async def resume_interrupt(
        self,
        state: State,
        interrupt_id: PathId,
        data: ResumeInterruptRequest,
        request: Request[object, object, State],
    ) -> ApiResponse[dict[str, str]]:
        """Resume a pending interrupt.

        Args:
            state: Application state.
            interrupt_id: Interrupt to resume.
            data: Resume payload.
            request: The incoming HTTP request.

        Returns:
            Confirmation envelope.
        """
        app_state: AppState = state.app_state
        store = _require_interrupt_store(app_state)
        auth_user = require_authenticated_user(request)
        return await _resolve_interrupt(
            store,
            interrupt_id,
            data,
            auth_user.username,
        )
