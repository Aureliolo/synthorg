# module-kind: complex_service
"""WebSocket handler for real-time event feeds.

Clients connect to ``/api/v1/ws`` and authenticate using a one-time
ticket obtained from ``POST /api/v1/auth/ws-ticket``.  Two auth
methods are supported (backward compatible):

1. **First-message auth** (preferred): connect without query params,
   then send ``{"action": "auth", "ticket": "<ticket>"}`` as the
   first message.  Keeps the ticket out of URLs, logs, and browser
   history.

2. **Query-param auth** (fallback for clients that cannot send a
   first message): connect to ``/api/v1/ws?ticket=<t>``. Validated
   before ``accept()`` so invalid tickets never upgrade.

After authentication, clients send JSON messages to subscribe/
unsubscribe from named channels with optional payload filters.
The server pushes ``WsEvent`` JSON on subscribed channels.
"""

import asyncio
import json
import time
from dataclasses import dataclass

from litestar import WebSocket
from litestar.channels import ChannelsPlugin, Subscriber
from litestar.datastructures import State
from litestar.exceptions import WebSocketDisconnect
from litestar.handlers import websocket

from synthorg._core.features import require_service
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.channels import ALL_CHANNELS, user_channel
from synthorg.api.controllers.ws_protocol import (
    channel_allowed,
    handle_message,
    matches_filters,
    parse_event_payload,
)
from synthorg.api.controllers.ws_revalidation import (
    _close_socket_safely,
    _periodic_revalidate,
)
from synthorg.api.guards import _READ_ROLES
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_WS_AUTH_OK,
    API_WS_AUTH_STAGE,
    API_WS_BACKPRESSURE_DROPPED,
    API_WS_CONNECTED,
    API_WS_DISCONNECTED,
    API_WS_EVENT_DROPPED,
    API_WS_FRAME_TIMEOUT,
    API_WS_SEND_FAILED,
    API_WS_TICKET_INVALID,
    API_WS_TRANSPORT_ERROR,
)
from synthorg.observability.state import ObservabilityStateSlice

logger = get_logger(__name__)

# Inbound size cap (subscribe/unsubscribe/auth/ping); 4 KiB DoS guard
# with headroom. Mirrored in ``ws_protocol.py`` for the protocol path.
_MAX_WS_MESSAGE_BYTES: int = 4096
# Outbound per-event cap; 32 KiB covers all current emitters. Mirror in
# ``web/src/utils/constants.ts`` as ``WS_MAX_MESSAGE_SIZE``.
_MAX_OUTBOUND_EVENT_BYTES: int = 32_768
# Per-client outbound queue depth before backpressure drops kick in.
_OUTBOUND_QUEUE_DEPTH: int = 64
# After this many consecutive backpressure drops within
# ``_WS_BACKPRESSURE_WINDOW_SECONDS``, treat the client as
# persistently slow and close the socket with 1013 ("try again
# later"). A healthy bursty client easily absorbs a few drops, but
# a client that cannot catch up across the window is wasting bus
# capacity for every other subscriber on its channels.
_WS_BACKPRESSURE_DROP_THRESHOLD: int = 32
_WS_BACKPRESSURE_WINDOW_SECONDS: float = 5.0
# RFC 6455 close code 1013: "Try Again Later". Used to signal a
# slow-client circuit-breaker trip without poisoning the reconnect
# loop -- the dashboard's WS store treats this as a reconnectable
# close (not an auth failure), so the same client can come back
# once it has caught up.
_WS_CLOSE_BACKPRESSURE: int = 1013

# Application-layer WS close codes (RFC 6455 §7.4.2: 4000-4999).
# ``_WS_CLOSE_SERVER_ERROR`` lives in :mod:`ws_revalidation`.
_WS_CLOSE_AUTH_FAILED: int = 4001
_WS_CLOSE_FORBIDDEN: int = 4003

# Upper bound on the ``?ticket=`` query parameter before it is even
# looked up in the ticket store. A ticket is ``token_urlsafe(N)`` where N is
# ``security.auth_token_bytes`` (max 64), so the longest legitimate ticket is
# ceil(64 * 4 / 3) = 86 chars; double it as a generous ceiling so an oversized
# value is rejected at the boundary instead of being passed to the store.
_MAX_TICKET_QUERY_LEN: int = 172

# Max concurrent unauthenticated (accepted-but-not-yet-authed) first-message
# connections from a single client IP. The first-message path must
# ``accept()`` before it can read the auth frame, so without this an attacker
# can open many sockets and never send auth, exhausting server resources
# before any credential check. RFC 6455 1008 (policy violation) is sent on
# rejection so a legitimate client can retry once below the cap.
_MAX_PREAUTH_CONNECTIONS_PER_IP: int = 10
_WS_CLOSE_POLICY_VIOLATION: int = 1008

# Process-local per-IP pre-auth connection counter. A slot is held only for
# the accept -> first-message-auth window and released the moment auth
# resolves (success or failure), so it bounds in-flight handshakes, not
# established authenticated connections.
_preauth_ip_counts: dict[str, int] = {}
_preauth_ip_lock: asyncio.Lock | None = None
_preauth_ip_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_preauth_lock() -> asyncio.Lock:
    """Return the pre-auth lock, rebinding it to the running loop.

    Instantiating ``asyncio.Lock`` at import time binds it to whatever
    loop is active then; under per-test event loops (pytest-asyncio)
    that cross-loop binding raises ``RuntimeError``. Build the lock
    lazily and rebind whenever the running loop changes.

    Returns:
        The ``asyncio.Lock`` bound to the current running loop.
    """
    global _preauth_ip_lock, _preauth_ip_lock_loop  # noqa: PLW0603
    current_loop = asyncio.get_running_loop()
    if _preauth_ip_lock is None or _preauth_ip_lock_loop is not current_loop:
        _preauth_ip_lock = asyncio.Lock()
        _preauth_ip_lock_loop = current_loop
    return _preauth_ip_lock


async def _acquire_preauth_slot(client_ip: str) -> bool:
    """Reserve a pre-auth handshake slot for *client_ip*.

    Returns:
        ``True`` when a slot was reserved, ``False`` when the IP is at the
        concurrent pre-auth cap.
    """
    async with _get_preauth_lock():
        count = _preauth_ip_counts.get(client_ip, 0)
        if count >= _MAX_PREAUTH_CONNECTIONS_PER_IP:
            return False
        _preauth_ip_counts[client_ip] = count + 1
        return True


async def _release_preauth_slot(client_ip: str) -> None:
    """Release a previously reserved pre-auth slot for *client_ip*."""
    async with _get_preauth_lock():
        count = _preauth_ip_counts.get(client_ip, 0)
        if count <= 1:
            _preauth_ip_counts.pop(client_ip, None)
        else:
            _preauth_ip_counts[client_ip] = count - 1


async def _validate_ticket(
    socket: WebSocket[object, object, State],
) -> AuthenticatedUser | None:
    """Validate the one-time ticket and return the user.

    Returns ``None`` and closes the socket if the ticket is
    missing, invalid, or expired.

    Returns:
        The ``AuthenticatedUser`` value when present, ``None`` otherwise.
    """
    ticket = socket.query_params.get("ticket")
    logger.debug(
        API_WS_AUTH_STAGE,
        stage="ticket_check",
        has_ticket=bool(ticket),
        client=str(socket.client),
    )
    if not ticket:
        logger.warning(API_WS_TICKET_INVALID, reason="missing_ticket")
        await socket.close(code=_WS_CLOSE_AUTH_FAILED, reason="Missing ticket")
        return None
    if len(ticket) > _MAX_TICKET_QUERY_LEN:
        # Reject oversized values before they ever reach the ticket store;
        # a legitimate ticket is at most 86 chars (see the constant).
        logger.warning(API_WS_TICKET_INVALID, reason="ticket_too_large")
        await socket.close(code=_WS_CLOSE_AUTH_FAILED, reason="Invalid ticket")
        return None

    app_state = socket.app.state["app_state"]
    ticket_store = require_service(
        app_state.slice(ApiCoreStateSlice).ticket_store, "WS Ticket Store"
    )
    user: AuthenticatedUser | None = await ticket_store.validate_and_consume(
        ticket,
    )
    if user is None:
        logger.warning(
            API_WS_TICKET_INVALID,
            reason="invalid_or_expired",
            client=str(socket.client),
        )
        await socket.close(
            code=_WS_CLOSE_AUTH_FAILED,
            reason="Invalid or expired ticket",
        )
        return None

    logger.debug(
        API_WS_AUTH_STAGE,
        stage="ticket_valid",
        user_id=user.user_id,
    )
    return user


async def _reject_auth(
    socket: WebSocket[object, object, State],
    log_reason: str,
    close_reason: str,
    *,
    code: int = _WS_CLOSE_AUTH_FAILED,
    **extra_kwargs: str,
) -> None:
    """Log a warning and close the socket for an auth rejection."""
    logger.warning(API_WS_TICKET_INVALID, reason=log_reason, **extra_kwargs)
    await socket.close(code=code, reason=close_reason)


async def _read_auth_message(
    socket: WebSocket[object, object, State],
) -> str | None:
    """Read and validate the first-message auth payload.

    Returns the ticket string, or ``None`` after closing the socket.
    The timeout is read once per connection from
    ``app_state.ws_auth_limits.auth_timeout_seconds``, which is baked in
    at startup by ``_apply_bridge_config`` from the operator-tunable
    ``api.ws_auth_timeout_seconds`` setting.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    app_state = socket.app.state["app_state"]
    try:
        data = await asyncio.wait_for(
            socket.receive_text(),
            timeout=app_state.ws_auth_limits.auth_timeout_seconds,
        )
    except TimeoutError:
        await _reject_auth(socket, "auth_timeout", "Auth timeout")
        return None
    except WebSocketDisconnect:
        logger.debug(API_WS_DISCONNECTED, reason="disconnect_during_auth")
        return None

    if len(data.encode()) > _MAX_WS_MESSAGE_BYTES:
        await _reject_auth(socket, "auth_too_large", "Auth message too large")
        return None

    try:
        msg = json.loads(data)
    except json.JSONDecodeError:
        await _reject_auth(socket, "invalid_auth_json", "Invalid auth message")
        return None

    if not isinstance(msg, dict) or msg.get("action") != "auth":
        action = msg.get("action", "") if isinstance(msg, dict) else ""
        await _reject_auth(
            socket,
            "expected_auth_action",
            "Expected auth action",
            action=str(action)[:64],
        )
        return None

    raw_ticket = msg.get("ticket")
    ticket: str | None = raw_ticket if isinstance(raw_ticket, str) else None
    if not ticket:
        await _reject_auth(socket, "missing_ticket_in_auth", "Missing ticket")
        return None

    return ticket


async def _auth_from_first_message(
    socket: WebSocket[object, object, State],
) -> AuthenticatedUser | None:
    """Authenticate via the first message after accept.

    Expects ``{"action": "auth", "ticket": "<ticket>"}``.  Returns
    ``None`` and closes the socket on invalid ticket, wrong message
    format, or timeout.

    Returns:
        The ``AuthenticatedUser`` value when present, ``None`` otherwise.
    """
    ticket = await _read_auth_message(socket)
    if ticket is None:
        return None

    app_state = socket.app.state["app_state"]
    ticket_store = require_service(
        app_state.slice(ApiCoreStateSlice).ticket_store, "WS Ticket Store"
    )
    user: AuthenticatedUser | None = await ticket_store.validate_and_consume(
        ticket,
    )
    if user is None:
        logger.warning(
            API_WS_TICKET_INVALID,
            reason="invalid_or_expired",
            client=str(socket.client),
        )
        await socket.close(
            code=_WS_CLOSE_AUTH_FAILED,
            reason="Invalid or expired ticket",
        )
        return None

    logger.debug(
        API_WS_AUTH_STAGE,
        stage="first_message_ticket_valid",
        user_id=user.user_id,
    )
    return user


async def _check_ws_role(
    socket: WebSocket[object, object, State],
    user: AuthenticatedUser,
) -> bool:
    """Verify the user has a role permitted for WebSocket access.

    Returns ``True`` if the role is valid.  On failure, closes the
    socket with a forbidden code and returns ``False``.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    logger.debug(
        API_WS_AUTH_STAGE,
        stage="role_check",
        user_id=user.user_id,
        role=str(user.role),
    )
    # Defense-in-depth: user.role is already validated as HumanRole by
    # Pydantic.  _READ_ROLES excludes SYSTEM (which has its own endpoints).
    # These checks guard against future changes to the role model or
    # read-role set.
    try:
        role = HumanRole(user.role)
    except ValueError:
        logger.warning(
            API_WS_TICKET_INVALID,
            reason="invalid_role",
            role=str(user.role),
        )
        await socket.close(code=_WS_CLOSE_FORBIDDEN, reason="Invalid role")
        return False

    if role not in _READ_ROLES:
        logger.warning(
            API_WS_TICKET_INVALID,
            reason="insufficient_role",
            role=role.value,
        )
        await socket.close(
            code=_WS_CLOSE_FORBIDDEN,
            reason="Insufficient permissions",
        )
        return False

    return True


@dataclass
class _BackpressureTracker:
    """Slow-consumer counter used by the outbound circuit breaker.

    Tracks consecutive backpressure drops within a rolling window;
    when the count crosses ``_WS_BACKPRESSURE_DROP_THRESHOLD`` the
    enqueue path closes the socket with 1013 instead of silently
    dropping yet another event. Reset on a successful enqueue so a
    client that recovers does not carry a stale tripping count
    indefinitely.

    State is mutated in place because each instance is scoped to a
    single WebSocket connection; there is no cross-connection sharing
    and asyncio's single-threaded scheduler means no concurrent
    callers. Backpressure is per-connection, not per-channel: a slow
    consumer that backs up on any one subscribed channel trips the
    breaker for the whole connection (intentional, to avoid letting a
    misbehaving client hog bus capacity).
    """

    consecutive_drops: int = 0
    window_started_at: float = 0.0

    def note_drop(self, *, now: float) -> bool:
        """Record one drop; return ``True`` when the threshold is crossed.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        if (
            self.consecutive_drops == 0
            or now - self.window_started_at > _WS_BACKPRESSURE_WINDOW_SECONDS
        ):
            self.window_started_at = now
            self.consecutive_drops = 1
        else:
            self.consecutive_drops += 1
        return self.consecutive_drops >= _WS_BACKPRESSURE_DROP_THRESHOLD

    def note_success(self) -> None:
        """Reset the counter after a successful enqueue.

        Clears ``window_started_at`` along with the count so the next
        drop starts a fresh window. Without this the next drop could
        land inside a stale window and delay the breaker trip by up
        to one window cycle.
        """
        if self.consecutive_drops:
            self.consecutive_drops = 0
            self.window_started_at = 0.0


async def _trip_breaker_and_close(
    *,
    backpressure: _BackpressureTracker,
    socket: WebSocket[object, object, State],
    clock: Clock | None,
    log_context: dict[str, object],
) -> None:
    """Record a backpressure drop and, if the breaker trips, close the socket.

    Shared trip-and-close path so every enqueue site (broadcast events
    in ``_on_event`` and control replies in ``_receive_loop``) feeds
    the same consecutive-drop counter and applies the same
    "close on threshold" policy. Without this single helper, the
    receive loop's ``QueueFull`` branch would only log a drop without
    advancing the breaker, and a chronically slow consumer could be
    closed only by a broadcast-side drop -- a client that pongs in
    time but stalls on control replies (or vice versa) would never
    trip the breaker even though it is wasting bus capacity.

    ``log_context`` is merged into the breaker-tripped warning so the
    caller's per-frame metadata (event type / channel / response
    shape) lands alongside the queue stats.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    # Clock seam (CLAUDE.md): tests inject ``FakeClock`` so the
    # rolling-window rollover in ``_BackpressureTracker.note_drop``
    # can be exercised deterministically. Production callers omit
    # ``clock`` and fall through to ``SystemClock``.
    resolved_clock = clock if clock is not None else SystemClock()
    now = resolved_clock.monotonic()
    tripped = backpressure.note_drop(now=now)
    if not tripped:
        return
    logger.warning(
        API_WS_BACKPRESSURE_DROPPED,
        reason="circuit_breaker_tripped",
        drop_threshold=_WS_BACKPRESSURE_DROP_THRESHOLD,
        window_seconds=_WS_BACKPRESSURE_WINDOW_SECONDS,
        **log_context,
    )
    # PEP 758: ``MemoryError`` / ``RecursionError`` must propagate
    # (CLAUDE.md async-helper rule); ``contextlib.suppress(Exception)``
    # would have swallowed them. Ordinary close failures (the socket
    # is already gone, the peer beat us to the disconnect) are still
    # ignored because the breaker has already taken the slow consumer
    # off the broadcast path.
    try:
        await socket.close(
            code=_WS_CLOSE_BACKPRESSURE,
            reason="Slow consumer; reconnect after catching up.",
        )
    except MemoryError, RecursionError:
        raise
    except Exception:  # noqa: BLE001, S110 -- ws close best-effort
        # Intentional suppression: see comment above. Logging the
        # close-time failure here would just add noise -- the
        # breaker-tripped warning above already told the operator
        # the consumer was slow.
        pass


async def _on_event(  # noqa: PLR0913
    event_data: bytes,
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    queue: asyncio.Queue[bytes],
    conn_user: AuthenticatedUser,
    backpressure: _BackpressureTracker | None = None,
    socket: WebSocket[object, object, State] | None = None,
    clock: Clock | None = None,
) -> None:
    """Filter a channel event and enqueue it for the outbound consumer.

    Applies the same subscription + access-control + filter checks as
    before, then additionally enforces ``_MAX_OUTBOUND_EVENT_BYTES`` and
    per-client backpressure. Events that pass all checks are enqueued
    onto the client's bounded outbound queue. Oversized events are
    dropped with ``API_WS_EVENT_DROPPED``; events that arrive while the
    queue is full are dropped with ``API_WS_BACKPRESSURE_DROPPED``.

    When a ``_BackpressureTracker`` is supplied, consecutive drops
    within ``_WS_BACKPRESSURE_WINDOW_SECONDS`` accumulate; crossing
    ``_WS_BACKPRESSURE_DROP_THRESHOLD`` trips the circuit breaker and
    closes the socket with 1013 ("try again later"). A healthy bursty
    client absorbs a few drops between successful enqueues without
    tripping; the tracker resets on every successful enqueue.
    """
    # Size-gate before parsing: a 30-MiB malformed frame should not
    # consume the JSON parser at all. We don't have channel/event_type
    # until after parse, but the DoS signal is the byte count itself,
    # and an oversized drop doesn't need the metadata to be useful.
    size_bytes = len(event_data)
    if size_bytes > _MAX_OUTBOUND_EVENT_BYTES:
        logger.warning(
            API_WS_EVENT_DROPPED,
            size_bytes=size_bytes,
            max_bytes=_MAX_OUTBOUND_EVENT_BYTES,
            reason="oversized_pre_parse",
        )
        return

    event = parse_event_payload(event_data)
    if event is None:
        return

    channel = event.get("channel", "")
    if channel not in subscribed:
        return
    if not channel_allowed(channel, conn_user):
        return

    channel_filters = filters.get(channel)
    if channel_filters and not matches_filters(
        event,
        channel,
        channel_filters,
    ):
        return

    event_type = event.get("event_type", "")

    try:
        queue.put_nowait(event_data)
        if backpressure is not None:
            backpressure.note_success()
    except asyncio.QueueFull:
        logger.warning(
            API_WS_BACKPRESSURE_DROPPED,
            channel=channel,
            event_type=str(event_type),
            queue_depth=queue.qsize(),
            max_depth=_OUTBOUND_QUEUE_DEPTH,
        )
        if backpressure is None or socket is None:
            return
        await _trip_breaker_and_close(
            backpressure=backpressure,
            socket=socket,
            clock=clock,
            log_context={
                "channel": channel,
                "event_type": str(event_type),
                "queue_depth": queue.qsize(),
                "max_depth": _OUTBOUND_QUEUE_DEPTH,
            },
        )


async def _outbound_consumer(
    socket: WebSocket[object, object, State],
    queue: asyncio.Queue[bytes],
) -> None:
    """Drain the per-client outbound queue and forward to the socket.

    Runs for the lifetime of the connection. ``WebSocketDisconnect`` is
    treated as a normal shutdown. Any other transport failure closes
    the socket with code 1011 and exits; the surrounding
    ``run_in_background`` context tears the subscription down.
    """
    # lint-allow: long-running-loop-kill-switch -- per-request WS consumer.
    while True:
        event_data = await queue.get()
        try:
            try:
                await socket.send_text(event_data.decode("utf-8"))
            except WebSocketDisconnect:
                logger.debug(API_WS_SEND_FAILED, reason="client_disconnected")
                return
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(logger, API_WS_SEND_FAILED, exc)
                await socket.close(code=1011, reason="Internal error")
                return
        finally:
            queue.task_done()


async def _send_auth_ok(socket: WebSocket[object, object, State]) -> None:
    """Send the ``auth_ok`` acknowledgement after ticket validation.

    Closes the client-side auth-state flash: clients SHOULD only set
    ``connected=true`` once this message arrives. Transport failures
    (clean disconnect or an unexpected ``send_text`` error) are
    treated as fatal: the socket is closed with 1011 on the generic
    path, mirroring ``_outbound_consumer``'s failure handling, and
    the exception is re-raised so the outer handler runs its cleanup.

    Raises:
        WebSocketDisconnect: Raised on the corresponding failure path.
        Exception: Raised on the corresponding failure path.
    """
    try:
        await socket.send_text(json.dumps({"action": "auth_ok"}))
    except WebSocketDisconnect:
        logger.debug(API_WS_SEND_FAILED, reason="disconnect_before_auth_ok")
        raise
    except Exception:
        logger.error(
            API_WS_SEND_FAILED,
            reason="send_error_auth_ok",
        )
        await socket.close(code=1011, reason="Internal error")
        raise
    # auth_ok is the handshake state transition: "authenticated" ->
    # "ready to serve". Logging rules require state transitions at
    # INFO so operational dashboards can see the connection lifecycle
    # without turning on DEBUG-level noise.
    logger.info(API_WS_AUTH_OK, client=str(socket.client))


async def _authenticate_ws(
    socket: WebSocket[object, object, State],
) -> tuple[AuthenticatedUser, bool] | None:
    """Run the two-path auth flow.

    Returns ``(user, already_accepted)`` on success, or ``None``
    (socket already closed) on failure.

    Returns:
        The ``tuple[AuthenticatedUser, bool]`` value when present, ``None`` otherwise.
    """
    ticket_param = socket.query_params.get("ticket")

    if ticket_param is not None:
        user = await _validate_ticket(socket)
        if user is None:
            return None
        # Role-gate the query-param path BEFORE the upgrade is accepted:
        # the ticket is validated pre-accept, so an insufficient-role client
        # is closed without ever completing the WebSocket handshake.
        if not await _check_ws_role(socket, user):
            return None
        return user, False

    # First-message path: must accept before reading the auth frame, so cap
    # concurrent pre-auth handshakes per client IP to bound a flood of
    # accepted-but-never-authenticated sockets.
    client_ip = socket.client.host if socket.client is not None else "unknown"
    if not await _acquire_preauth_slot(client_ip):
        logger.warning(
            API_WS_AUTH_STAGE,
            stage="preauth_ip_limit",
            client=str(socket.client),
        )
        await socket.close(
            code=_WS_CLOSE_POLICY_VIOLATION,
            reason="Too many pending connections",
        )
        return None
    try:
        await socket.accept()
        user = await _auth_from_first_message(socket)
    finally:
        await _release_preauth_slot(client_ip)
    if user is None:
        return None
    # The first-message path must accept before it can read the auth frame,
    # so the role gate runs immediately after authentication -- the earliest
    # point possible -- closing an insufficient-role socket before any
    # channel subscription or event flow.
    if not await _check_ws_role(socket, user):
        return None
    return user, True


def _resolve_channels_plugin(
    socket: WebSocket[object, object, State],
) -> ChannelsPlugin | None:
    """Resolve the ChannelsPlugin from app.plugins.

    Litestar's DI does not reliably inject plugin instances into
    WebSocket handlers (the parameter is misidentified as a query
    param, causing a Litestar-internal 4500 close before the
    handler runs); resolve from ``socket.app.plugins`` directly.

    Returns:
        The ``ChannelsPlugin`` value when present, ``None`` otherwise.
    """
    for plugin in socket.app.plugins:
        if isinstance(plugin, ChannelsPlugin):
            return plugin
    return None


async def _setup_connection(
    socket: WebSocket[object, object, State],
    user: AuthenticatedUser,
    *,
    already_accepted: bool,
) -> tuple[ChannelsPlugin, Subscriber] | None:
    """Resolve plugin, accept the connection, and subscribe to channels.

    Returns ``(channels_plugin, subscriber)`` on success, or ``None``
    (socket already closed) on failure.

    Note: the query-param (ticket) path role-checks before ``accept()``,
    so an insufficient-role client is rejected without an upgrade. The
    first-message path must accept to read the auth frame, so there an
    insufficient-role client receives the upgrade followed immediately by
    close code 4003 -- inherent to reading over an established WS
    connection. Either way no in-scope role reaches subscription.

    Returns:
        The ``tuple[ChannelsPlugin, Subscriber]`` value when present,
        ``None`` otherwise.
    """
    channels_plugin = _resolve_channels_plugin(socket)
    if channels_plugin is None:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="channels_plugin_not_registered",
        )
        await socket.close(code=1011, reason="Internal error")
        return None

    socket.scope["user"] = user
    if not already_accepted:
        await socket.accept()

    # Subscribe to all shared channels + the user's private channel.
    # This runs *before* ``auth_ok`` so that the server is already
    # ready to broadcast by the time the client flips
    # ``connected=true`` and starts listening -- emitting ``auth_ok``
    # earlier would open a window where events published to channels
    # the client has been auto-subscribed to could be dropped because
    # ``subscriber.run_in_background`` hasn't started yet.
    user_ch = user_channel(user.user_id)
    all_subs = [*ALL_CHANNELS, user_ch]
    try:
        subscriber = await channels_plugin.subscribe(all_subs)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="subscribe_failed",
            client=str(socket.client),
            user_id=user.user_id,
        )
        await socket.close(code=1011, reason="Internal error")
        return None

    # Track presence. If the presence registry throws (e.g., the
    # AppState slot is swapped mid-connection), we need to undo the
    # earlier ``subscribe()`` so the subscriber isn't left live for a
    # connection that never finished establishing.
    app_state = socket.app.state["app_state"]
    try:
        require_service(
            app_state.slice(ApiCoreStateSlice).user_presence, "User Presence"
        ).connect(user.user_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="presence_connect_failed",
            client=str(socket.client),
            user_id=user.user_id,
        )
        try:
            await channels_plugin.unsubscribe(subscriber)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                API_WS_TRANSPORT_ERROR,
                reason="unsubscribe_after_presence_connect_failure",
                client=str(socket.client),
            )
        await socket.close(code=1011, reason="Internal error")
        return None

    # Now that subscriptions + presence are established, send the
    # auth acknowledgement so the client can flip ``connected=true``
    # knowing the server is ready to receive and broadcast. If the
    # send fails for *any* reason (clean disconnect or an unexpected
    # error) we tear down the subscription + presence state so they
    # don't leak past a half-open connection.
    try:
        await _send_auth_ok(socket)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await channels_plugin.unsubscribe(subscriber)
        except Exception as unsub_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(unsub_exc)
            logger.error(
                API_WS_TRANSPORT_ERROR,
                reason="unsubscribe_after_auth_ok_failure",
                client=str(socket.client),
            )
        require_service(
            app_state.slice(ApiCoreStateSlice).user_presence, "User Presence"
        ).disconnect(user.user_id)
        return None

    logger.info(
        API_WS_CONNECTED,
        client=str(socket.client),
        user_id=user.user_id,
    )
    return channels_plugin, subscriber


def _record_ws_connection_opened(socket: WebSocket[object, object, State]) -> None:
    """Increment the WS active-connection gauge.

    The Prometheus collector lives on ``app_state``; the helper checks
    presence defensively so a controller running without the
    observability stack (rare; mostly tests) does not blow up at
    setup. ``Gauge.inc()`` is internally thread-safe, so no explicit
    lock is needed between the WS handler coroutine and the metrics
    scrape thread.
    """
    app_state = socket.app.state["app_state"]
    collector = app_state.slice(ObservabilityStateSlice).prometheus_collector
    if collector is None:
        return
    collector.inc_ws_active_connections()


def _record_ws_connection_closed(
    socket: WebSocket[object, object, State],
    *,
    duration_sec: float,
) -> None:
    """Observe the WS lifetime histogram and decrement the active gauge."""
    app_state = socket.app.state["app_state"]
    collector = app_state.slice(ObservabilityStateSlice).prometheus_collector
    if collector is None:
        return
    collector.record_ws_connection_lifetime(
        transport="websocket",
        duration_sec=duration_sec,
    )
    collector.dec_ws_active_connections()


async def _teardown_connection(
    socket: WebSocket[object, object, State],
    user: AuthenticatedUser,
    channels_plugin: ChannelsPlugin,
    subscriber: Subscriber,
    consumer_task: asyncio.Task[None],
) -> None:
    """Cancel the consumer, unsubscribe, disconnect, and log.

    Extracted from ``ws_handler``'s ``finally`` block to keep the
    handler under the project's cyclomatic-complexity cap. The flow
    is: cancel the outbound consumer; if the *outer* handler task was
    cancelled (server shutdown / client-bound timeout), defer the
    re-raise until after unsubscribe + user_presence cleanup + the
    ``API_WS_DISCONNECTED`` log have run, so subscriber/presence
    state stays consistent with the socket actually closing.

    Raises:
        outer_cancelled_exc: Raised on the corresponding failure path.
    """
    outer_cancelled_exc: asyncio.CancelledError | None = None
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError as exc:
        current = asyncio.current_task()
        if current is not None and current.cancelling() > 0:
            outer_cancelled_exc = exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="outbound_consumer_failed",
            client=str(socket.client),
        )
    try:
        await channels_plugin.unsubscribe(subscriber)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_WS_TRANSPORT_ERROR,
            error="Failed to unsubscribe",
            client=str(socket.client),
        )
    app_state = socket.app.state["app_state"]
    # Presence disconnect is best-effort: if the user_presence
    # registry has been swapped or raises on teardown, we still need
    # to emit ``API_WS_DISCONNECTED`` and re-raise any deferred
    # cancellation so the outer scheduler unwinds cleanly.
    try:
        require_service(
            app_state.slice(ApiCoreStateSlice).user_presence, "User Presence"
        ).disconnect(user.user_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="presence_disconnect_failed",
            client=str(socket.client),
            user_id=user.user_id,
        )
    logger.info(API_WS_DISCONNECTED, client=str(socket.client))
    if outer_cancelled_exc is not None:
        raise outer_cancelled_exc


# Defense-in-depth: opt signals Litestar's auth middleware to skip
# this handler.  The middleware is already HTTP-only (ScopeType.HTTP)
# and the WS path is regex-excluded, so this is a tertiary safeguard.
@websocket("/ws", opt={"exclude_from_auth": True})
async def ws_handler(
    socket: WebSocket[object, object, State],
) -> None:
    """Handle WebSocket connections with channel subscriptions.

    Supports two authentication methods (backward compatible):

    1. **First-message auth** (preferred): connect without ``?ticket``,
       accept the upgrade, then send ``{"action": "auth", "ticket": "..."}``
       as the first message.  Keeps the ticket out of URLs and logs.

    2. **Query-param auth** (fallback): connect with ``?ticket=<ticket>``.
       Validated and consumed before ``accept()``.
    """
    auth_result = await _authenticate_ws(socket)
    if auth_result is None:
        return
    user, already_accepted = auth_result

    # Role-gating is performed inside ``_authenticate_ws`` for both auth
    # paths (before ``accept()`` on the query-param path, immediately after
    # authentication on the first-message path), so no socket reaches here
    # without a permitted role.
    setup = await _setup_connection(socket, user, already_accepted=already_accepted)
    if setup is None:
        return
    channels_plugin, subscriber = setup

    # Wall-clock start so the teardown path can observe connection
    # lifetime into the ``synthorg_ws_connection_lifetime_seconds``
    # histogram. ``time.monotonic`` so a wall-clock NTP step does not
    # push the bucket bound.
    # lint-allow: clock-seam -- Litestar @websocket route handler has
    # no clock-injection seam; lifetime histogram needs NTP-immune monotonic
    connection_started_at = time.monotonic()
    _record_ws_connection_opened(socket)

    # Auto-subscribe to the user's private channel.
    user_ch = user_channel(user.user_id)
    subscribed: set[str] = {user_ch}
    filters: dict[str, dict[str, str]] = {}

    # Per-client outbound queue isolates a single slow consumer from the
    # broadcast pipeline. The consumer task drains it and writes to the
    # socket; the channel callback enqueues with backpressure-aware drop.
    outbound_queue: asyncio.Queue[bytes] = asyncio.Queue(
        maxsize=_OUTBOUND_QUEUE_DEPTH,
    )
    backpressure_tracker = _BackpressureTracker()

    async def _event_callback(event_data: bytes) -> None:
        """Run event callback."""
        await _on_event(
            event_data,
            subscribed,
            filters,
            outbound_queue,
            user,
            backpressure=backpressure_tracker,
            socket=socket,
        )

    # Structured concurrency for the WS background workers (CLAUDE.md):
    # ``TaskGroup`` cancels siblings on any task's failure and turns the
    # cancel/await choreography into a single ``async with`` block. The
    # outbound consumer + the periodic revalidator are long-running; we
    # cancel them explicitly when ``_receive_loop`` returns so the group
    # can exit. Wrapping in ``ExceptionGroup`` lets a real failure in
    # either worker surface to operators while ``CancelledError`` from
    # our own teardown is treated as the expected shutdown path.
    consumer_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.TaskGroup() as tg:
            consumer_task = tg.create_task(
                _outbound_consumer(socket, outbound_queue),
            )
            revalidate_task = tg.create_task(
                _periodic_revalidate(socket, user),
            )
            try:
                async with subscriber.run_in_background(_event_callback):
                    # Share the per-connection breaker between the
                    # broadcast path (``_event_callback`` -> ``_on_event``)
                    # and the control-frame path (``_receive_loop``). A
                    # client that pongs in time but stalls on broadcasts
                    # (or vice versa) advances the same consecutive-drop
                    # counter, so the breaker policy fires regardless of
                    # which enqueue site noticed the saturation.
                    await _receive_loop(
                        socket,
                        subscribed,
                        filters,
                        user,
                        outbound_queue,
                        backpressure=backpressure_tracker,
                    )
            finally:
                # Long-running workers won't exit on their own; cancel
                # them so the TaskGroup can release.
                revalidate_task.cancel()
                consumer_task.cancel()
    except* asyncio.CancelledError:
        # Expected: receive loop returned, we cancelled the workers.
        pass
    except* Exception as eg:  # noqa: BLE001 -- TaskGroup boundary
        # Real failure from a background task -- log each and proceed
        # with teardown so subscriber/presence cleanup still runs.
        for exc in eg.exceptions:
            # A worker's reraise_critical surfaced a process-fatal error
            # into the group; propagate it instead of folding it into
            # routine teardown logging.
            reraise_critical(exc)
            logger.warning(
                API_WS_TRANSPORT_ERROR,
                stage="ws_worker_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    finally:
        if consumer_task is not None:
            _record_ws_connection_closed(
                socket,
                # lint-allow: clock-seam -- pairs with the route-handler
                # baseline above; same no-injection-seam justification
                duration_sec=time.monotonic() - connection_started_at,
            )
            await _teardown_connection(
                socket,
                user,
                channels_plugin,
                subscriber,
                consumer_task,
            )


async def _receive_loop(  # noqa: PLR0913 -- optional backpressure + clock + timeout kwargs
    socket: WebSocket[object, object, State],
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    conn_user: AuthenticatedUser,
    outbound_queue: asyncio.Queue[bytes],
    *,
    frame_timeout_seconds: float | None = None,
    backpressure: _BackpressureTracker | None = None,
    clock: Clock | None = None,
) -> None:
    """Process client subscribe/unsubscribe commands.

    Control replies (``subscribed`` / ``unsubscribed`` / ``pong``) are
    routed through the outbound queue rather than written to the socket
    directly so ``_outbound_consumer`` remains the single writer and
    control frames cannot interleave with broadcast events mid-frame.
    The non-blocking ``put_nowait`` enqueue protects the receive loop
    from wedging on a full queue -- if ``_outbound_consumer`` has
    exited or cannot keep up, the control reply is dropped (logged
    via ``API_WS_BACKPRESSURE_DROPPED``) and the socket continues to
    accept new inbound frames rather than hanging forever on an
    unbounded ``await queue.put``.

    The per-frame ``frame_timeout_seconds`` budget caps how long the
    loop waits for the next inbound frame.  A connection that goes
    silent past the budget is closed with policy code 1008 so a
    silent client cannot indefinitely hold a slot (DoS prevention).
    Defaults to ``app_state.ws_auth_limits.frame_timeout_seconds``
    (registered setting ``api.ws_frame_timeout_seconds``, default 30).

    Raises:
        Exception: Raised on the corresponding failure path.
    """
    if frame_timeout_seconds is None:
        app_state = socket.app.state["app_state"]
        frame_timeout_seconds = app_state.ws_auth_limits.frame_timeout_seconds
    try:
        # lint-allow: long-running-loop-kill-switch -- per-request WS receive.
        while True:
            try:
                data = await asyncio.wait_for(
                    socket.receive_text(),
                    timeout=frame_timeout_seconds,
                )
            except TimeoutError:
                logger.info(
                    API_WS_FRAME_TIMEOUT,
                    user_id=conn_user.user_id,
                    client=str(socket.client),
                    timeout_seconds=frame_timeout_seconds,
                )
                await _close_socket_safely(
                    socket,
                    code=1008,  # RFC 6455 Policy Violation
                    reason="frame timeout",
                )
                return
            # Snapshot ``subscribed`` / ``filters`` before applying the
            # handler so that if the ack cannot be enqueued (queue full)
            # we can roll back the mutation. Without the rollback,
            # server and client subscription state would diverge -- the
            # server thinks the client is on the channel but the client
            # never saw the ack, so subsequent unsubscribes get lost.
            subscribed_snapshot = set(subscribed)
            filters_snapshot = {k: dict(v) for k, v in filters.items()}
            response = handle_message(
                data,
                subscribed,
                filters,
                conn_user,
            )
            try:
                outbound_queue.put_nowait(response.encode("utf-8"))
                if backpressure is not None:
                    # Control replies count toward the breaker's
                    # "healthy enqueue" signal: a client that
                    # successfully receives an ack has proved it can
                    # absorb at least one outbound frame, which is
                    # the same liveness signal a successful broadcast
                    # enqueue carries. Resetting the counter here
                    # prevents a stale broadcast-side drop count from
                    # closing a client that has since recovered.
                    backpressure.note_success()
            except asyncio.QueueFull:
                # Restore pre-handler state so client and server stay
                # in sync; the drop is logged for backpressure metrics.
                subscribed.clear()
                subscribed.update(subscribed_snapshot)
                filters.clear()
                filters.update(filters_snapshot)
                logger.warning(
                    API_WS_BACKPRESSURE_DROPPED,
                    reason="control_reply_queue_full",
                    user_id=conn_user.user_id,
                    client=str(socket.client),
                )
                # Feed the same breaker the broadcast path feeds so
                # a chronically slow consumer is closed regardless of
                # which enqueue site filled the queue. Without this
                # call the consecutive-drop counter could only ever
                # advance from ``_on_event``, letting control-reply
                # drops accumulate indefinitely.
                if backpressure is not None:
                    await _trip_breaker_and_close(
                        backpressure=backpressure,
                        socket=socket,
                        clock=clock,
                        log_context={
                            "user_id": conn_user.user_id,
                            "client": str(socket.client),
                            "reason_origin": "control_reply_queue_full",
                            "queue_depth": outbound_queue.qsize(),
                            "max_depth": _OUTBOUND_QUEUE_DEPTH,
                        },
                    )
    except WebSocketDisconnect:
        logger.debug(API_WS_DISCONNECTED, reason="client_disconnect")
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            user_id=conn_user.user_id,
            client=str(socket.client),
        )
        raise
