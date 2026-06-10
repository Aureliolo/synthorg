"""SSE streaming machinery for the event-stream controller.

The hub-subscription generator (``_sse_event_stream``) and its support
code: keepalive-interval resolution, the per-connection sliding-window
revalidation limiter, the user-revocation check, event serialisation,
and one-tick revalidation. ``EventStreamController`` imports these from
``synthorg.api.controllers.events._sse``; kept out of ``stream`` so the
controller module stays under the controller LOC cap.
"""

import asyncio
import json as _json
from collections.abc import AsyncIterator
from typing import Final

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.guards import _READ_ROLES
from synthorg.api.state import AppState
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import StreamEvent
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.auth.config import AUTH_REVALIDATE_INTERVAL_SECONDS
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.clock import SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.engine.classification.sinks import _SlidingWindowRateLimiter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_CLIENT_CONNECTED,
    EVENT_STREAM_CLIENT_DISCONNECTED,
    EVENT_STREAM_PROJECTION_FAILED,
)
from synthorg.observability.metrics_hub import record_client_disconnect
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

_SSE_KEEPALIVE_FALLBACK_SECONDS: Final[float] = 30.0
"""Internal constant by design: fallback keepalive interval used only
when the resolver is unavailable; the canonical operator-tunable value
is ``api.sse_keepalive_seconds``.

Mirrors the registry default for ``api.sse_keepalive_seconds`` so a
test harness or anonymous stream that bypasses :class:`AppState` still
emits keepalives at the documented cadence.
"""

# Defaults when no AppState/config is wired (anonymous boot, unit
# harness). Mirror the ``auth_revalidate_*`` registry defaults; the
# WS revalidation loop uses the same sliding-window model + settings.
_AUTH_REVALIDATE_WINDOW_FALLBACK_SECONDS: Final[float] = 60.0
_AUTH_REVALIDATE_MAX_FAILURES_FALLBACK: Final[int] = 5


async def _resolve_sse_keepalive_seconds(app_state: AppState | None) -> float:
    """Resolve the SSE keepalive interval through the settings chain.

    Falls back to :data:`_SSE_KEEPALIVE_FALLBACK_SECONDS` when the
    application state has no :class:`ConfigResolver` wired (test
    harness, anonymous boot path).  Resolver outages collapse to the
    same fallback so a transient settings outage cannot break the
    keepalive cadence on a long-lived stream.

    Returns:
        Resulting numeric value.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state is None or app_state.slice(SettingsStateSlice).config_resolver is None:
        return _SSE_KEEPALIVE_FALLBACK_SECONDS
    try:
        resolver = config_resolver_of(app_state)
        return await resolver.get_float("api", "sse_keepalive_seconds")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="failed to resolve api.sse_keepalive_seconds; using fallback",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=_SSE_KEEPALIVE_FALLBACK_SECONDS,
        )
        return _SSE_KEEPALIVE_FALLBACK_SECONDS


def _build_revalidation_limiter(
    app_state: AppState | None,
) -> _SlidingWindowRateLimiter:
    """Build a per-connection sliding-window limiter for SSE revalidation.

    "Shared" with WS means the same *model + settings*, not a shared
    instance: like ``_periodic_revalidate`` (WS), each stream gets its
    own limiter so one connection's transient failures cannot evict
    another's.

    Mirrors ``_periodic_revalidate`` (WS): a flaky persistence layer
    that interleaves one success between failure clusters cannot keep
    a stale-auth stream alive, because failures age out of the window
    rather than resetting a streak on success. Window + ceiling come
    from ``api.auth_revalidate_window_seconds`` /
    ``api.auth_revalidate_max_failures`` (resolved into ``AppState``
    at startup), shared with the WS loop.

    Returns:
        ``_SlidingWindowRateLimiter`` instance.
    """
    if app_state is not None:
        window = float(app_state.ws_auth_limits.auth_revalidate_window_seconds)
        max_failures = app_state.ws_auth_limits.auth_revalidate_max_failures
    else:
        window = _AUTH_REVALIDATE_WINDOW_FALLBACK_SECONDS
        max_failures = _AUTH_REVALIDATE_MAX_FAILURES_FALLBACK
    # The SSE loop runs one revalidation tick per
    # ``AUTH_REVALIDATE_INTERVAL_SECONDS`` (10 min). A window measured
    # in wall-clock seconds shorter than ``max_failures`` ticks can
    # never saturate -- each failed tick ages out before the next one
    # -- so a prolonged persistence outage would keep a stale-auth
    # stream open indefinitely (fail-open). Clamp so ``max_failures``
    # consecutive failed ticks fall inside the window while isolated
    # old failures still age out (mirrors the WS ``_periodic_revalidate``
    # clamp; the two paths must stay in lockstep).
    effective_window = max(
        window,
        float(AUTH_REVALIDATE_INTERVAL_SECONDS) * max_failures,
    )
    return _SlidingWindowRateLimiter(
        max_events=max_failures,
        window_seconds=effective_window,
    )


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
    backend error). Callers admit ``ok=False`` ticks into the shared
    sliding-window limiter (``api.auth_revalidate_window_seconds`` /
    ``api.auth_revalidate_max_failures``) before tearing down the
    stream.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    try:
        db_user = await persistence_of(app_state).users.get(user_id)
    except Exception as exc:
        reraise_critical(exc)
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
    session_store = app_state.slice(ApiCoreStateSlice).session_store
    if (
        session_id is not None
        and session_store is not None
        and session_store.is_revoked(session_id)
    ):
        return "session_revoked", True
    return None, True


def _require_hub(app_state: AppState) -> EventStreamHub:
    """Return the hub or raise when unavailable.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    hub = app_state.slice(CommunicationStateSlice).event_stream_hub
    if hub is None:
        msg = "Event stream not configured"
        raise NotFoundError(msg)
    return hub


async def _serialise_stream_event(
    event: StreamEvent,
    session_id: str,
) -> dict[str, str] | None:
    """Render a hub event as an SSE frame, or ``None`` on serialise failure.

    Extracted from :func:`_sse_event_stream` so the loop body stays
    under the McCabe / branch / statement ceilings. Failures are
    logged at WARNING and skipped; the parent loop should ``continue``.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    try:
        data = _json.dumps(event.model_dump(mode="json"))
    except Exception as serialize_exc:
        reraise_critical(serialize_exc)
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


async def _run_revalidation_tick(
    *,
    app_state: AppState,
    user: AuthenticatedUser,
    failure_limiter: _SlidingWindowRateLimiter,
) -> dict[str, str] | None:
    """Execute one revalidation check; return a ``revoked`` frame or None.

    Sliding-window failure model (shared with ``_periodic_revalidate``
    on the WS side): a transient persistence error is admitted into
    ``failure_limiter``; the stream is torn down only once the window
    saturates (``api.auth_revalidate_max_failures`` errors within
    ``api.auth_revalidate_window_seconds``). Failures age out of the
    window instead of resetting a streak on success, so a flaky
    backend interleaving one success cannot hold a stale-auth stream
    open. A genuine revocation (deleted / demoted / session revoked)
    tears down immediately. Returns ``None`` when the loop continues.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    reason, ok = await _user_revocation_reason(
        app_state,
        user.user_id,
        user.session_id,
    )
    if not ok:
        admitted = await failure_limiter.take(user.user_id)
        if not admitted:
            return {
                "event": "revoked",
                "data": _json.dumps({"reason": "backend_unavailable"}),
            }
        return None
    if reason is not None:
        return {
            "event": "revoked",
            "data": _json.dumps({"reason": reason}),
        }
    return None


async def _sse_event_stream(
    hub: EventStreamHub,
    session_id: str,
    *,
    app_state: AppState | None = None,
    user: AuthenticatedUser | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events from the hub for the given session.

    When ``app_state`` and ``user`` are supplied, the loop tracks an
    independent revalidation deadline (``AUTH_REVALIDATE_INTERVAL_SECONDS``,
    the single cadence shared with the WS revalidation loop) and fires
    it even on busy streams that never hit a keepalive timeout. On
    revocation, yields a final ``revoked`` event and terminates the
    stream. Transient persistence errors are absorbed by a shared
    sliding-window limiter (``api.auth_revalidate_window_seconds`` /
    ``api.auth_revalidate_max_failures``) before escalating.

    Raises:
        CancelledError: Raised on the corresponding failure path.
        Exception: Raised on the corresponding failure path.
    """
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
        # Shared sliding-window limiter (same model + settings as the
        # WS ``_periodic_revalidate`` loop).
        failure_limiter = _build_revalidation_limiter(app_state)
        # Use ``app_state.clock.monotonic()`` so tests inject FakeClock
        # rather than monkey-patching ``asyncio.get_event_loop().time``.
        # The bare loop timer is still acceptable for async waits below.
        clock = app_state.clock if app_state is not None else SystemClock()
        loop_now = clock.monotonic()
        next_keepalive_ts = loop_now + keepalive_seconds
        # When auth context is absent (anonymous / unit-test stream), arming
        # the revalidation deadline at ``loop_now`` would make ``timeout``
        # collapse to 0 on the first iteration and busy-loop the wait_for.
        # Only arm the timer when there is something to revalidate.
        next_revalidate_ts: float | None = (
            loop_now + AUTH_REVALIDATE_INTERVAL_SECONDS if revalidation_armed else None
        )
        # lint-allow: long-running-loop-kill-switch -- per-request SSE stream; lifetime bounded by client connection (CancelledError on disconnect)  # noqa: E501
        while True:
            now = clock.monotonic()
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
            now = clock.monotonic()
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
                next_revalidate_ts = now + AUTH_REVALIDATE_INTERVAL_SECONDS
                revoked_frame = await _run_revalidation_tick(
                    app_state=app_state,
                    user=user,
                    failure_limiter=failure_limiter,
                )
                if revoked_frame is not None:
                    disconnect_reason = "cancelled"
                    yield revoked_frame
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
