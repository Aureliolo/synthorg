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
from contextlib import suppress
from typing import Final

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.guards import _READ_ROLES
from synthorg.api.state import AppState
from synthorg.communication.event_stream.stream import (
    EventStreamHub,
    EventStreamSubscription,
)
from synthorg.communication.event_stream.types import StreamEvent
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.auth.config import AUTH_REVALIDATE_INTERVAL_SECONDS
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.clock import SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.resilience import (
    SlidingWindowEventLimiter,
    build_revalidation_limiter,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_CLIENT_CONNECTED,
    EVENT_STREAM_CLIENT_DISCONNECTED,
    EVENT_STREAM_PROJECTION_FAILED,
)
from synthorg.observability.events.security import SECURITY_AUTH_FAILED
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
) -> SlidingWindowEventLimiter:
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
        ``SlidingWindowEventLimiter`` instance.
    """
    if app_state is not None:
        window = float(app_state.ws_auth_limits.auth_revalidate_window_seconds)
        max_failures = app_state.ws_auth_limits.auth_revalidate_max_failures
    else:
        window = _AUTH_REVALIDATE_WINDOW_FALLBACK_SECONDS
        max_failures = _AUTH_REVALIDATE_MAX_FAILURES_FALLBACK
    # The SSE loop runs one revalidation tick per
    # ``AUTH_REVALIDATE_INTERVAL_SECONDS`` (10 min); the shared builder
    # clamps the window so ``max_failures`` consecutive failed ticks fall
    # inside it (fail-closed) while isolated old failures still age out.
    # The WS ``_periodic_revalidate`` path uses the same builder so the
    # two stay in lockstep.
    return build_revalidation_limiter(
        max_failures=max_failures,
        window_seconds=window,
        interval_seconds=AUTH_REVALIDATE_INTERVAL_SECONDS,
    )


async def _user_revocation_reason(
    app_state: AppState,
    user: AuthenticatedUser,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)``: reason is None when still authorised.

    Checks the user record (deleted / role-missing / demoted), the
    JWT session-revocation set (an admin ``DELETE /sessions/{jti}``
    must kick a live SSE stream within one revalidation interval), and,
    for API-key-authenticated streams (no JWT session id), the
    originating API key itself (revoked / expired).

    ``ok`` is False when the persistence call itself failed (transient
    backend error). Callers admit ``ok=False`` ticks into the shared
    sliding-window limiter (``api.auth_revalidate_window_seconds`` /
    ``api.auth_revalidate_max_failures``) before tearing down the
    stream.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    try:
        db_user = await persistence_of(app_state).users.get(user.user_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="sse_revalidate_persistence_error",
            user_id=user.user_id,
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
        user.session_id is not None
        and session_store is not None
        and session_store.is_revoked(user.session_id)
    ):
        return "session_revoked", True
    # API-key streams carry no JWT jti; the session-revocation set never
    # covers them. Re-inspect the originating key so revocation / expiry
    # tears the stream down within one revalidation interval.
    if user.session_id is None and user.api_key_id is not None:
        return await _api_key_revocation_reason(app_state, user.api_key_id)
    return None, True


async def _api_key_revocation_reason(
    app_state: AppState,
    api_key_id: str,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)`` for an API-key-authenticated SSE stream.

    Re-fetches the API key by id (O(1)) and reports ``"api_key_revoked"``
    when the record is missing or revoked, ``"api_key_expired"`` once
    ``expires_at`` has passed (compared against the injected clock). A
    transient backend error yields ``ok=False`` so the caller admits it
    to the shared sliding-window limiter rather than tearing down.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    try:
        api_key = await persistence_of(app_state).api_keys.get(api_key_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="sse_revalidate_api_key_persistence_error",
            api_key_id=api_key_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, False
    if api_key is None or api_key.revoked:
        return "api_key_revoked", True
    if api_key.expires_at is not None and api_key.expires_at <= app_state.clock.now():
        return "api_key_expired", True
    return None, True


async def _session_ownership_reason(
    app_state: AppState,
    ag_ui_session_id: str,
    user: AuthenticatedUser,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)`` for AG-UI session ownership.

    The AG-UI ``session_id`` is the task id. Only the human who filed
    the task (``Task.requested_by_user_id``) or a CEO may subscribe to
    its event stream. A missing task (or a non-matching requester)
    yields ``"session_not_owned"``; a transient backend error yields
    ``ok=False`` so the caller admits it to the revalidation limiter.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    if user.role is HumanRole.CEO:
        return None, True
    try:
        task = await persistence_of(app_state).tasks.get(ag_ui_session_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EVENT_STREAM_PROJECTION_FAILED,
            note="sse_ownership_persistence_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, False
    if task is None or task.requested_by_user_id != user.user_id:
        return "session_not_owned", True
    return None, True


async def assert_sse_session_access(
    app_state: AppState,
    ag_ui_session_id: str,
    user: AuthenticatedUser,
) -> None:
    """Enforce AG-UI session ownership at the SSE handshake.

    Raises a 404 (never 403) when the caller is neither the task's
    requester nor a CEO, so a caller cannot enumerate other users'
    session ids by status code. A transient backend error fails closed
    (also 404): the stream is denied rather than opened on unverified
    ownership.

    Raises:
        NotFoundError: When ownership cannot be confirmed for the caller.
    """
    reason, ok = await _session_ownership_reason(app_state, ag_ui_session_id, user)
    if reason is None and ok:
        return
    logger.warning(
        SECURITY_AUTH_FAILED,
        reason="session_not_owned" if ok else "ownership_check_unavailable",
        session_id=ag_ui_session_id[:8],
        user_id=user.user_id,
    )
    msg = "Session not found"
    raise NotFoundError(msg)


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

    The frame carries the event ``id`` so the browser EventSource records
    it as ``lastEventId`` and replays it via the ``Last-Event-ID`` header
    on reconnect. Keepalive / revoked frames deliberately omit ``id`` so
    they never clobber the client's resume cursor.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    try:
        data = _json.dumps(event.model_dump(mode="json"))
    except Exception as serialize_exc:  # noqa: BLE001 -- criticals re-raised
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
    # Strip CR/LF: the SSE wire format is newline-delimited, so a stray
    # newline in the id would inject a spurious field line. Event ids are
    # in-process opaque strings, but sanitise defensively at the boundary.
    safe_id = event.id.replace("\r", "").replace("\n", "")
    return {"event": event.type.value, "data": data, "id": safe_id}


async def _run_revalidation_tick(
    *,
    app_state: AppState,
    user: AuthenticatedUser,
    session_id: str,
    failure_limiter: SlidingWindowEventLimiter,
) -> dict[str, str] | None:
    """Execute one revalidation check; return a ``revoked`` frame or None.

    Sliding-window failure model (shared with ``_periodic_revalidate``
    on the WS side): a transient persistence error is admitted into
    ``failure_limiter``; the stream is torn down only once the window
    saturates (``api.auth_revalidate_max_failures`` errors within
    ``api.auth_revalidate_window_seconds``). Failures age out of the
    window instead of resetting a streak on success, so a flaky
    backend interleaving one success cannot hold a stale-auth stream
    open. A genuine revocation (deleted / demoted / session revoked /
    API key revoked or expired / session ownership lost) tears down
    immediately. Returns ``None`` when the loop continues.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    reason, ok = await _user_revocation_reason(app_state, user)
    if ok and reason is None:
        # Ownership is re-checked every tick so a deleted / reassigned
        # task tears the stream down within one revalidation interval.
        reason, ok = await _session_ownership_reason(app_state, session_id, user)
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


async def _revalidate_once(
    *,
    app_state: AppState,
    user: AuthenticatedUser,
    failure_limiter: SlidingWindowEventLimiter,
) -> dict[str, str] | None:
    """One revalidation check for a session-less SSE stream.

    Like :func:`_run_revalidation_tick` minus the task-session-ownership
    check (these streams are not tied to a task session). Returns a
    ``revoked`` frame on genuine revocation or limiter saturation, else
    ``None``.

    Returns:
        The ``revoked`` frame dict, or ``None`` when the stream continues.
    """
    reason, ok = await _user_revocation_reason(app_state, user)
    if not ok:
        admitted = await failure_limiter.take(user.user_id)
        if not admitted:
            return {
                "event": "revoked",
                "data": _json.dumps({"reason": "backend_unavailable"}),
            }
        return None
    if reason is not None:
        return {"event": "revoked", "data": _json.dumps({"reason": reason})}
    return None


async def revalidated_sse_stream(
    inner: AsyncIterator[dict[str, str]],
    *,
    app_state: AppState,
    user: AuthenticatedUser,
) -> AsyncIterator[dict[str, str]]:
    """Wrap a session-less SSE event iterator with periodic auth revalidation.

    Races *inner* against an ``AUTH_REVALIDATE_INTERVAL_SECONDS`` deadline
    (the single cadence shared with the WS + events-hub revalidation loops)
    so a long-lived stream (e.g. a model pull) is torn down within one
    interval of the user being deleted / demoted / their session or API key
    revoked. Transient persistence errors are absorbed by the shared
    sliding-window limiter before escalating to ``backend_unavailable``.

    Yields:
        The inner stream's events, then a terminal ``revoked`` event if the
        user's auth is revoked mid-stream.
    """
    failure_limiter = _build_revalidation_limiter(app_state)
    clock = app_state.clock
    next_revalidate_ts = clock.monotonic() + AUTH_REVALIDATE_INTERVAL_SECONDS
    inner_iter = inner.__aiter__()
    pending: asyncio.Task[dict[str, str]] | None = None
    try:
        # lint-allow: long-running-loop-kill-switch -- per-request SSE stream; lifetime bounded by client connection (CancelledError on disconnect) + auth revocation  # noqa: E501
        while True:
            timeout = max(0.0, next_revalidate_ts - clock.monotonic())
            if pending is None:
                pending = asyncio.ensure_future(anext(inner_iter))
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout)
            except TimeoutError:
                revoked = await _revalidate_once(
                    app_state=app_state,
                    user=user,
                    failure_limiter=failure_limiter,
                )
                if revoked is not None:
                    yield revoked
                    return
                next_revalidate_ts = (
                    clock.monotonic() + AUTH_REVALIDATE_INTERVAL_SECONDS
                )
                continue
            except StopAsyncIteration:
                return
            pending = None
            yield event
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


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
    subscription: EventStreamSubscription | None = None
    try:
        # Subscribe inside the try block so a CancelledError /
        # MemoryError raised during pre-loop setup
        # (``_resolve_sse_keepalive_seconds``,
        # ``asyncio.get_event_loop().time()``) cannot leave a dead
        # subscriber attached to the hub: ``finally`` always runs
        # ``hub.unsubscribe`` and tolerates ``queue is None`` when the
        # subscribe itself raised.
        subscription = await hub.subscribe(session_id)
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
                    subscription.get(),
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
                    session_id=session_id,
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
            if subscription is not None:
                await hub.unsubscribe(subscription)
        finally:
            logger.info(
                EVENT_STREAM_CLIENT_DISCONNECTED,
                session_id=session_id,
            )
            record_client_disconnect(
                transport="sse",
                reason=disconnect_reason,
            )
