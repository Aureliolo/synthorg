"""Event stream and interrupt controllers.

Provides SSE event streaming at ``/events/stream`` and a polling
fallback for interrupts at ``/interrupts``.
"""

import asyncio
import json as _json
from collections.abc import AsyncIterator  # noqa: TC003
from datetime import UTC, datetime
from typing import Annotated, Any

from litestar import Controller, Request, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from litestar.response import ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.auth.config import SSE_REVALIDATE_INTERVAL_SECONDS
from synthorg.api.auth.models import AuthenticatedUser
from synthorg.api.dto import ApiResponse
from synthorg.api.errors import ApiValidationError, NotFoundError, UnauthorizedError
from synthorg.api.guards import _READ_ROLES, require_approval_roles, require_read_access
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptResolution,
    InterruptStore,
    InterruptType,
    ResumeDecision,
)
from synthorg.communication.event_stream.stream import EventStreamHub  # noqa: TC001
from synthorg.communication.event_stream.types import StreamEvent  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_CLIENT_CONNECTED,
    EVENT_STREAM_CLIENT_DISCONNECTED,
    EVENT_STREAM_INTERRUPT_NOT_FOUND,
    EVENT_STREAM_PROJECTION_FAILED,
)

logger = get_logger(__name__)

_SSE_KEEPALIVE_SECONDS = 30.0
# Session IDs flow into a hub keyed on the value -- restrict the alphabet
# to alphanumerics + dash + underscore to block path-traversal-shaped or
# control-character session IDs reaching the hub.
_SESSION_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,128}$"

# Maximum consecutive revalidation failures (transient persistence
# blips) before the SSE stream terminates so the client can reconnect
# against a healthy replica (#1599).
_SSE_REVALIDATE_MAX_FAILURES: int = 3


async def _user_revocation_reason(
    app_state: AppState,
    user_id: str,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)``: reason is None when still authorised.

    ``ok`` is False when the persistence call itself failed (transient
    backend error). Callers tolerate ``_SSE_REVALIDATE_MAX_FAILURES``
    consecutive ``ok=False`` ticks before tearing down the stream.
    """
    try:
        db_user = await app_state.persistence.users.get(user_id)
    except Exception as exc:
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="sse_revalidate_persistence_error",
            user_id=user_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, False
    if db_user is None:
        return "user_deleted", True
    role = getattr(db_user, "role", None)
    if role is None:
        return "user_role_missing", True
    if role not in _READ_ROLES:
        return "role_demoted", True
    return None, True


# ── DTOs ─────────────────────────────────────────────────────────


class ResumeInterruptRequest(BaseModel):
    """Request body for resuming an interrupt."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    decision: ResumeDecision | None = Field(
        default=None,
        description="Approval decision (TOOL_APPROVAL only)",
    )
    feedback: NotBlankStr | None = Field(
        default=None,
        description="Feedback text (TOOL_APPROVAL only)",
    )
    response: NotBlankStr | None = Field(
        default=None,
        description="Clarification response (INFO_REQUEST only)",
    )


class InterruptResponse(BaseModel):
    """Interrupt item returned by the polling API."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr
    type: InterruptType
    session_id: NotBlankStr
    agent_id: NotBlankStr
    created_at: str
    timeout_seconds: float
    tool_name: NotBlankStr | None = None
    tool_args: dict[str, object] | None = None
    evidence_package_id: NotBlankStr | None = None
    question: NotBlankStr | None = None
    context_snippet: NotBlankStr | None = None


# ── Helpers ──────────────────────────────────────────────────────


def _require_hub(app_state: AppState) -> EventStreamHub:
    hub = app_state.event_stream_hub
    if hub is None:
        msg = "Event stream not configured"
        raise NotFoundError(msg)
    return hub


def _require_interrupt_store(app_state: AppState) -> InterruptStore:
    store = app_state.interrupt_store
    if store is None:
        msg = "Interrupt store not configured"
        raise NotFoundError(msg)
    return store


def _require_auth(request: Request[Any, Any, Any]) -> AuthenticatedUser:
    auth_user = request.scope.get("user")
    if not isinstance(auth_user, AuthenticatedUser):
        msg = "Authentication required"
        raise UnauthorizedError(msg)
    return auth_user


def _validate_resume_payload(
    interrupt: Interrupt,
    data: ResumeInterruptRequest,
) -> None:
    """Validate resume payload matches the interrupt type.

    Args:
        interrupt: The pending interrupt being resumed.
        data: The client's resume payload.

    Raises:
        ApiValidationError: If required fields are missing.
    """
    if interrupt.type == InterruptType.TOOL_APPROVAL and data.decision is None:
        msg = "TOOL_APPROVAL interrupts require a decision"
        raise ApiValidationError(msg)
    if interrupt.type == InterruptType.INFO_REQUEST and data.response is None:
        msg = "INFO_REQUEST interrupts require a response"
        raise ApiValidationError(msg)


async def _resolve_interrupt(
    store: InterruptStore,
    interrupt_id: str,
    data: ResumeInterruptRequest,
    resolved_by: str,
) -> ApiResponse[dict[str, str]]:
    """Shared logic for both resume endpoints.

    Args:
        store: The interrupt store.
        interrupt_id: The interrupt to resume.
        data: The resume payload.
        resolved_by: Identity of the resolver.

    Returns:
        Confirmation envelope.

    Raises:
        NotFoundError: If interrupt doesn't exist or is no longer pending.
        ApiValidationError: If payload doesn't match interrupt type.
    """
    interrupt = await store.get(interrupt_id)
    if interrupt is None:
        logger.warning(
            EVENT_STREAM_INTERRUPT_NOT_FOUND,
            interrupt_id=interrupt_id,
        )
        msg = f"Interrupt {interrupt_id!r} not found"
        raise NotFoundError(msg)

    _validate_resume_payload(interrupt, data)

    resolution = InterruptResolution(
        interrupt_id=interrupt_id,
        decision=data.decision,
        feedback=data.feedback,
        response=data.response,
        resolved_at=datetime.now(UTC),
        resolved_by=resolved_by,
    )
    resolved = await store.resolve(resolution)
    if resolved is None:
        msg = f"Interrupt {interrupt_id!r} is no longer pending"
        raise NotFoundError(msg)

    return ApiResponse(data={"status": "resumed"})


# ── SSE stream ───────────────────────────────────────────────────


async def _sse_event_stream(
    hub: EventStreamHub,
    session_id: str,
    *,
    app_state: AppState | None = None,
    user: AuthenticatedUser | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events from the hub for the given session.

    When ``app_state`` and ``user`` are supplied, every Nth keepalive
    re-checks the user's role -- bounded by
    ``SSE_REVALIDATE_INTERVAL_SECONDS`` (#1599). On revocation, yields
    a final ``revoked`` event and terminates the stream. Tolerates
    ``_SSE_REVALIDATE_MAX_FAILURES`` transient persistence errors
    before escalating.
    """
    keepalives_per_revalidate = max(
        1,
        int(SSE_REVALIDATE_INTERVAL_SECONDS / _SSE_KEEPALIVE_SECONDS),
    )
    keepalive_count = 0
    consecutive_failures = 0
    queue = hub.subscribe(session_id)
    logger.info(
        EVENT_STREAM_CLIENT_CONNECTED,
        session_id=session_id,
    )
    try:
        while True:
            try:
                event: StreamEvent = await asyncio.wait_for(
                    queue.get(),
                    timeout=_SSE_KEEPALIVE_SECONDS,
                )
                try:
                    data = _json.dumps(event.model_dump(mode="json"))
                except MemoryError, RecursionError:
                    raise
                except Exception as serialize_exc:
                    logger.warning(
                        EVENT_STREAM_PROJECTION_FAILED,
                        session_id=session_id,
                        event_id=event.id,
                        note="Failed to serialize event, skipping",
                        error_type=type(serialize_exc).__name__,
                        error=safe_error_description(serialize_exc),
                    )
                    continue
                yield {"event": event.type.value, "data": data}
            except TimeoutError:
                yield {"event": "keepalive", "data": "{}"}
                keepalive_count += 1
                if (
                    app_state is not None
                    and user is not None
                    and keepalive_count >= keepalives_per_revalidate
                ):
                    keepalive_count = 0
                    reason, ok = await _user_revocation_reason(
                        app_state,
                        user.user_id,
                    )
                    if not ok:
                        consecutive_failures += 1
                        if consecutive_failures >= _SSE_REVALIDATE_MAX_FAILURES:
                            yield {
                                "event": "revoked",
                                "data": _json.dumps(
                                    {"reason": "backend_unavailable"},
                                ),
                            }
                            return
                        continue
                    consecutive_failures = 0
                    if reason is not None:
                        yield {
                            "event": "revoked",
                            "data": _json.dumps({"reason": reason}),
                        }
                        return
    finally:
        # Unsubscribe must run before the disconnect log: a raise here
        # leaves the queue subscribed to the hub, which would leak
        # memory as new events keep enqueueing to a dead client. Log
        # the disconnect regardless, then re-raise so the caller (and
        # the SSE iterator harness) sees the failure.
        try:
            hub.unsubscribe(session_id, queue)
        finally:
            logger.info(
                EVENT_STREAM_CLIENT_DISCONNECTED,
                session_id=session_id,
            )


# ── Controllers ──────────────────────────────────────────────────


class EventStreamController(Controller):
    """AG-UI SSE event stream and interrupt resume."""

    path = "/events"
    tags = ("events",)

    @get(
        "/stream",
        media_type="text/event-stream",
        guards=[require_read_access],
    )
    async def stream(
        self,
        state: State,
        request: Request[Any, Any, Any],
        session_id: Annotated[
            NotBlankStr,
            Parameter(
                max_length=QUERY_MAX_LENGTH,
                pattern=_SESSION_ID_PATTERN,
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
        """
        app_state: AppState = state.app_state
        hub = _require_hub(app_state)
        user = getattr(request, "user", None)
        return ServerSentEvent(
            content=_sse_event_stream(
                hub,
                session_id,
                app_state=app_state,
                user=user if isinstance(user, AuthenticatedUser) else None,
            ),
        )

    @post(
        "/resume/{interrupt_id:str}",
        guards=[require_approval_roles],
        status_code=200,
    )
    async def resume_interrupt(
        self,
        state: State,
        interrupt_id: PathId,
        data: ResumeInterruptRequest,
        request: Request[Any, Any, Any],
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
        auth_user = _require_auth(request)
        return await _resolve_interrupt(
            store,
            interrupt_id,
            data,
            auth_user.username,
        )


class InterruptController(Controller):
    """Polling fallback for interrupt management."""

    path = "/interrupts"
    tags = ("interrupts",)

    @get(guards=[require_read_access])
    async def list_interrupts(
        self,
        state: State,
        session_id: Annotated[
            NotBlankStr | None,
            Parameter(
                max_length=QUERY_MAX_LENGTH,
                pattern=_SESSION_ID_PATTERN,
            ),
        ] = None,
    ) -> ApiResponse[tuple[InterruptResponse, ...]]:
        """List pending interrupts.

        Args:
            state: Application state.
            session_id: Optional session filter.

        Returns:
            List of pending interrupts.
        """
        app_state: AppState = state.app_state
        store = _require_interrupt_store(app_state)
        pending = await store.list_pending(session_id=session_id)
        items = tuple(
            InterruptResponse(
                id=i.id,
                type=i.type,
                session_id=i.session_id,
                agent_id=i.agent_id,
                created_at=i.created_at.isoformat(),
                timeout_seconds=i.timeout_seconds,
                tool_name=i.tool_name,
                tool_args=i.tool_args,
                evidence_package_id=i.evidence_package_id,
                question=i.question,
                context_snippet=i.context_snippet,
            )
            for i in pending
        )
        return ApiResponse(data=items)

    @post(
        "/{interrupt_id:str}/resume",
        guards=[require_approval_roles],
        status_code=200,
    )
    async def resume(
        self,
        state: State,
        interrupt_id: PathId,
        data: ResumeInterruptRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[dict[str, str]]:
        """Resume a pending interrupt via polling API.

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
        auth_user = _require_auth(request)
        return await _resolve_interrupt(
            store,
            interrupt_id,
            data,
            auth_user.username,
        )
