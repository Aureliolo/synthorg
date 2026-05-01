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
from synthorg.api.guards import _READ_ROLES, require_approval_roles, require_read_access
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
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
from synthorg.core.domain_errors import (
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_VALIDATION_FAILED
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_CLIENT_CONNECTED,
    EVENT_STREAM_CLIENT_DISCONNECTED,
    EVENT_STREAM_INTERRUPT_NOT_FOUND,
    EVENT_STREAM_PROJECTION_FAILED,
)
from synthorg.observability.metrics_hub import record_client_disconnect

logger = get_logger(__name__)

_SSE_KEEPALIVE_FALLBACK_SECONDS = 30.0
"""Internal constant by design: fallback keepalive interval used only
when the resolver is unavailable; the canonical operator-tunable value
is ``api.sse_keepalive_seconds``.

Mirrors the registry default for ``api.sse_keepalive_seconds`` so a
test harness or anonymous stream that bypasses :class:`AppState` still
emits keepalives at the documented cadence.
"""


async def _resolve_sse_keepalive_seconds(app_state: AppState | None) -> float:
    """Resolve the SSE keepalive interval through the settings chain.

    Falls back to :data:`_SSE_KEEPALIVE_FALLBACK_SECONDS` when the
    application state has no :class:`ConfigResolver` wired (test
    harness, anonymous boot path).  Resolver outages collapse to the
    same fallback so a transient settings outage cannot break the
    keepalive cadence on a long-lived stream.
    """
    if app_state is None or not getattr(app_state, "has_config_resolver", False):
        return _SSE_KEEPALIVE_FALLBACK_SECONDS
    try:
        return await app_state.config_resolver.get_float("api", "sse_keepalive_seconds")
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="failed to resolve api.sse_keepalive_seconds; using fallback",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=_SSE_KEEPALIVE_FALLBACK_SECONDS,
        )
        return _SSE_KEEPALIVE_FALLBACK_SECONDS


# Session IDs flow into a hub keyed on the value -- restrict the alphabet
# to alphanumerics + dash + underscore to block path-traversal-shaped or
# control-character session IDs reaching the hub.
_SESSION_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,128}$"

# Maximum consecutive revalidation failures (transient persistence
# blips) before the SSE stream terminates so the client can reconnect
# against a healthy replica.
_SSE_REVALIDATE_MAX_FAILURES: int = 3


async def _user_revocation_reason(
    app_state: AppState,
    user_id: str,
    session_id: str | None,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)``: reason is None when still authorised.

    Checks the user record (deleted / role-missing / demoted) **and**
    the session-revocation set (an admin ``DELETE /sessions/{jti}``
    must kick a live SSE stream within one revalidation interval).

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
    if (
        session_id is not None
        and app_state.has_session_store
        and app_state.session_store.is_revoked(session_id)
    ):
        return "session_revoked", True
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
        ValidationError: If required fields are missing.
    """
    if interrupt.type == InterruptType.TOOL_APPROVAL and data.decision is None:
        msg = "TOOL_APPROVAL interrupts require a decision"
        logger.warning(
            API_VALIDATION_FAILED,
            reason="resume_payload_missing_field",
            interrupt_type=interrupt.type.value,
            missing_field="decision",
        )
        raise ValidationError(msg)
    if interrupt.type == InterruptType.INFO_REQUEST and data.response is None:
        msg = "INFO_REQUEST interrupts require a response"
        logger.warning(
            API_VALIDATION_FAILED,
            reason="resume_payload_missing_field",
            interrupt_type=interrupt.type.value,
            missing_field="response",
        )
        raise ValidationError(msg)


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
        ValidationError: If payload doesn't match interrupt type.
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


async def _serialise_stream_event(
    event: StreamEvent,
    session_id: str,
) -> dict[str, str] | None:
    """Render a hub event as an SSE frame, or ``None`` on serialise failure.

    Extracted from :func:`_sse_event_stream` so the loop body stays
    under the McCabe / branch / statement ceilings. Failures are
    logged at WARNING and skipped; the parent loop should ``continue``.
    """
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
        return None
    return {"event": event.type.value, "data": data}


class _RevalidationVerdict(BaseModel):
    """Outcome of one revalidation tick.

    Attributes:
        consecutive_failures: Updated transient-failure counter the
            caller threads back into the loop.
        revoked_event: SSE frame to yield when the user is no longer
            authorised (or the persistence backend is repeatedly
            unavailable). ``None`` when the loop should keep running.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    consecutive_failures: int
    revoked_event: dict[str, str] | None = None


async def _run_revalidation_tick(
    *,
    app_state: AppState,
    user: AuthenticatedUser,
    consecutive_failures: int,
) -> _RevalidationVerdict:
    """Execute one revalidation check and return what the loop should do.

    Centralises the failure-counter / role-check / session-revocation
    decision tree so :func:`_sse_event_stream` does not exceed the
    McCabe complexity ceiling. The caller advances its
    ``next_revalidate_ts`` regardless of the verdict.
    """
    reason, ok = await _user_revocation_reason(
        app_state,
        user.user_id,
        user.session_id,
    )
    if not ok:
        new_failures = consecutive_failures + 1
        if new_failures >= _SSE_REVALIDATE_MAX_FAILURES:
            return _RevalidationVerdict(
                consecutive_failures=new_failures,
                revoked_event={
                    "event": "revoked",
                    "data": _json.dumps({"reason": "backend_unavailable"}),
                },
            )
        return _RevalidationVerdict(consecutive_failures=new_failures)
    if reason is not None:
        return _RevalidationVerdict(
            consecutive_failures=0,
            revoked_event={
                "event": "revoked",
                "data": _json.dumps({"reason": reason}),
            },
        )
    return _RevalidationVerdict(consecutive_failures=0)


async def _sse_event_stream(  # noqa: PLR0915, PLR0912, C901
    hub: EventStreamHub,
    session_id: str,
    *,
    app_state: AppState | None = None,
    user: AuthenticatedUser | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events from the hub for the given session.

    When ``app_state`` and ``user`` are supplied, the loop tracks an
    independent revalidation deadline (``SSE_REVALIDATE_INTERVAL_SECONDS``)
    and fires it even on busy streams that never hit a keepalive
    timeout. On revocation, yields a final ``revoked`` event
    and terminates the stream. Tolerates ``_SSE_REVALIDATE_MAX_FAILURES``
    transient persistence errors before escalating.
    """
    consecutive_failures = 0
    # Track the disconnect reason by exit path so the
    # ``synthorg_client_disconnects_total`` metric reflects the real
    # cause: ``cancelled`` for revocation / asyncio.CancelledError,
    # ``transport_error`` for unexpected exceptions, and
    # ``client_initiated`` for clean drops (the default).
    disconnect_reason = "client_initiated"
    queue: asyncio.Queue[StreamEvent] | None = None
    try:
        # Subscribe inside the try block so a CancelledError /
        # MemoryError raised during pre-loop setup
        # (``_resolve_sse_keepalive_seconds``,
        # ``asyncio.get_event_loop().time()``) cannot leave a dead
        # subscriber attached to the hub: ``finally`` always runs
        # ``hub.unsubscribe`` and tolerates ``queue is None`` when the
        # subscribe itself raised.
        queue = await hub.subscribe(session_id)
        logger.info(
            EVENT_STREAM_CLIENT_CONNECTED,
            session_id=session_id,
        )
        revalidation_armed = app_state is not None and user is not None
        keepalive_seconds = await _resolve_sse_keepalive_seconds(app_state)
        loop_now = asyncio.get_event_loop().time()
        next_keepalive_ts = loop_now + keepalive_seconds
        # When auth context is absent (anonymous / unit-test stream), arming
        # the revalidation deadline at ``loop_now`` would make ``timeout``
        # collapse to 0 on the first iteration and busy-loop the wait_for.
        # Only arm the timer when there is something to revalidate.
        next_revalidate_ts: float | None = (
            loop_now + SSE_REVALIDATE_INTERVAL_SECONDS if revalidation_armed else None
        )
        while True:
            now = asyncio.get_event_loop().time()
            if next_revalidate_ts is None:
                timeout = max(0.0, next_keepalive_ts - now)
            else:
                timeout = max(0.0, min(next_keepalive_ts, next_revalidate_ts) - now)
            try:
                event: StreamEvent = await asyncio.wait_for(
                    queue.get(),
                    timeout=timeout,
                )
                frame = await _serialise_stream_event(event, session_id)
                if frame is not None:
                    yield frame
            except TimeoutError:
                # Timer fired; decide which deadline expired. Both can
                # be due simultaneously after a long-blocking event was
                # delivered; emit keepalive first, revalidate second.
                pass
            now = asyncio.get_event_loop().time()
            if now >= next_keepalive_ts:
                yield {"event": "keepalive", "data": "{}"}
                next_keepalive_ts = now + keepalive_seconds
            if (
                revalidation_armed
                and next_revalidate_ts is not None
                and now >= next_revalidate_ts
                and app_state is not None
                and user is not None
            ):
                next_revalidate_ts = now + SSE_REVALIDATE_INTERVAL_SECONDS
                verdict = await _run_revalidation_tick(
                    app_state=app_state,
                    user=user,
                    consecutive_failures=consecutive_failures,
                )
                consecutive_failures = verdict.consecutive_failures
                if verdict.revoked_event is not None:
                    disconnect_reason = "cancelled"
                    yield verdict.revoked_event
                    return
    except asyncio.CancelledError:
        disconnect_reason = "cancelled"
        raise
    except Exception as exc:
        disconnect_reason = "transport_error"
        # Surface the underlying transport failure so an operator can
        # tell broken-pipe / TLS-reset / generator-misuse apart from
        # the routine ``cancelled`` path before the final disconnect
        # log fires in the ``finally`` block. Never embed
        # ``str(exc)`` directly -- transport errors can carry
        # request-side credentials in their messages.
        logger.warning(
            EVENT_STREAM_CLIENT_DISCONNECTED,
            session_id=session_id,
            reason="transport_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    finally:
        # Unsubscribe must run before the disconnect log: a raise here
        # leaves the queue subscribed to the hub, which would leak
        # memory as new events keep enqueueing to a dead client. Log
        # the disconnect regardless, then re-raise so the caller (and
        # the SSE iterator harness) sees the failure.
        try:
            # Tolerate the case where ``hub.subscribe`` itself raised:
            # there is nothing to unsubscribe in that branch.
            if queue is not None:
                await hub.unsubscribe(session_id, queue)
        finally:
            logger.info(
                EVENT_STREAM_CLIENT_DISCONNECTED,
                session_id=session_id,
            )
            record_client_disconnect(
                transport="sse",
                reason=disconnect_reason,
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
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("interrupts.resume", key="user"),
        ],
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
