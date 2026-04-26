"""WebSocket handler for real-time event feeds.

Clients connect to ``/api/v1/ws`` and authenticate using a one-time
ticket obtained from ``POST /api/v1/auth/ws-ticket``.  Two auth
methods are supported (backward compatible):

1. **First-message auth** (preferred): connect without query params,
   then send ``{"action": "auth", "ticket": "<ticket>"}`` as the
   first message.  Keeps the ticket out of URLs, logs, and browser
   history.

2. **Query-param auth** (legacy): connect to ``/api/v1/ws?ticket=<t>``.
   Validated before ``accept()`` so invalid tickets never upgrade.

After authentication, clients send JSON messages to subscribe/
unsubscribe from named channels with optional payload filters.
The server pushes ``WsEvent`` JSON on subscribed channels.
"""

import asyncio
import contextlib
import json
from typing import Any

from litestar import WebSocket  # noqa: TC002
from litestar.channels import ChannelsPlugin
from litestar.exceptions import WebSocketDisconnect
from litestar.handlers import websocket

from synthorg.api.auth.config import WS_REVALIDATE_INTERVAL_SECONDS
from synthorg.api.auth.models import AuthenticatedUser  # noqa: TC001
from synthorg.api.channels import ALL_CHANNELS, user_channel
from synthorg.api.controllers.ws_protocol import (
    channel_allowed,
    handle_message,
    matches_filters,
    parse_event_payload,
)
from synthorg.api.guards import _READ_ROLES, HumanRole
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_WS_AUTH_OK,
    API_WS_AUTH_STAGE,
    API_WS_BACKPRESSURE_DROPPED,
    API_WS_CONNECTED,
    API_WS_DISCONNECTED,
    API_WS_EVENT_DROPPED,
    API_WS_SEND_FAILED,
    API_WS_TICKET_INVALID,
    API_WS_TRANSPORT_ERROR,
)
from synthorg.observability.events.security import SECURITY_SESSION_REVOKED

logger = get_logger(__name__)

# Inbound (client -> server) control-message size cap. Subscribe/unsubscribe
# /auth/ping payloads max out around 3 KiB even at full filter limits, so 4
# KiB is a tight DoS guard with deliberate headroom. Mirrored in
# ``ws_protocol.py`` for the subscribe/unsubscribe path; kept here for
# first-message-auth validation, which runs before the protocol helpers.
_MAX_WS_MESSAGE_BYTES: int = 4096
# Outbound (server -> client) per-event size cap. Largest realistic event
# today is COMPANY_UPDATED at ~25-30 KB for a 15+ dept org. 32 KiB covers
# all current emitters with headroom; oversized events are dropped without
# closing the socket so a single producer cannot nuke the channel for
# every subscriber. Mirror in ``web/src/utils/constants.ts`` as
# ``WS_MAX_MESSAGE_SIZE``.
_MAX_OUTBOUND_EVENT_BYTES: int = 32_768
# Per-client outbound queue depth before backpressure drops kick in. Sized
# generously (~64 events) so a brief stall doesn't drop legitimate traffic
# while still bounding memory at ~2 MB worst case (64 * 32 KiB).
_OUTBOUND_QUEUE_DEPTH: int = 64

# Application-layer WS close codes (RFC 6455 §7.4.2: 4000-4999).
_WS_CLOSE_AUTH_FAILED: int = 4001
_WS_CLOSE_FORBIDDEN: int = 4003
_WS_CLOSE_SERVER_ERROR: int = 4011

# Maximum consecutive revalidation failures (transient persistence
# blips) before the connection is closed with a server-error code so
# the client can reconnect rather than receive stale-auth events.
_WS_REVALIDATE_MAX_FAILURES: int = 3


async def _close_socket_safely(
    socket: WebSocket[Any, Any, Any],
    *,
    code: int,
    reason: str,
) -> None:
    """Best-effort close that logs but does not propagate teardown errors.

    The socket may already be torn down (client disconnected, network
    blip), but we still want the revocation decision recorded AND the
    close failure logged so operators can diagnose half-open sockets
    after a session-revocation event (#1599).
    """
    try:
        await socket.close(code=code, reason=reason)
    except Exception as exc:
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="socket_close_failed_during_revoke",
            client=str(socket.client),
            close_code=code,
            close_reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _periodic_revalidate(
    socket: WebSocket[Any, Any, Any],
    user: AuthenticatedUser,
    *,
    interval_seconds: int = WS_REVALIDATE_INTERVAL_SECONDS,
) -> None:
    """Re-load the user every *interval_seconds* and close on revocation.

    Bounds the post-revocation window to one tick: an admin who
    deletes the user, demotes the role below ``_READ_ROLES``, or
    revokes the session sees the WS close within ``interval_seconds``
    rather than at next disconnect.

    Tolerates ``_WS_REVALIDATE_MAX_FAILURES`` consecutive transient
    persistence failures before escalating -- one DB blip should not
    flap every WS client, but a sustained outage should surface so
    clients can reconnect against a healthy replica.
    """
    consecutive_failures = 0
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            app_state = socket.app.state["app_state"]
            db_user = await app_state.persistence.users.get(user.user_id)
        except Exception as exc:
            consecutive_failures += 1
            logger.warning(
                API_WS_TRANSPORT_ERROR,
                reason="revalidate_persistence_error",
                client=str(socket.client),
                user_id=user.user_id,
                consecutive_failures=consecutive_failures,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if consecutive_failures >= _WS_REVALIDATE_MAX_FAILURES:
                await _close_socket_safely(
                    socket,
                    code=_WS_CLOSE_SERVER_ERROR,
                    reason="Revalidation backend unavailable",
                )
                return
            continue

        consecutive_failures = 0
        revoke_reason = _revocation_reason(db_user)
        if revoke_reason is not None:
            logger.info(
                SECURITY_SESSION_REVOKED,
                client=str(socket.client),
                user_id=user.user_id,
                reason=revoke_reason,
                trigger="ws_periodic_revalidate",
            )
            await _close_socket_safely(
                socket,
                code=_WS_CLOSE_FORBIDDEN,
                reason=f"Session revoked ({revoke_reason})",
            )
            return


def _revocation_reason(db_user: object | None) -> str | None:
    """Return the rejection reason or None when the user is still authorised."""
    if db_user is None:
        return "user_deleted"
    role = getattr(db_user, "role", None)
    if role is None:
        return "user_role_missing"
    if role not in _READ_ROLES:
        return "role_demoted"
    return None


async def _validate_ticket(
    socket: WebSocket[Any, Any, Any],
) -> AuthenticatedUser | None:
    """Validate the one-time ticket and return the user.

    Returns ``None`` and closes the socket if the ticket is
    missing, invalid, or expired.
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

    app_state = socket.app.state["app_state"]
    user: AuthenticatedUser | None = app_state.ticket_store.validate_and_consume(
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
    socket: WebSocket[Any, Any, Any],
    log_reason: str,
    close_reason: str,
    *,
    code: int = _WS_CLOSE_AUTH_FAILED,
    **extra_kwargs: str,
) -> None:
    """Log a warning and close the socket for an auth rejection."""
    logger.warning(API_WS_TICKET_INVALID, reason=log_reason, **extra_kwargs)
    await socket.close(code=code, reason=close_reason)


async def _read_auth_message(  # noqa: PLR0911
    socket: WebSocket[Any, Any, Any],
) -> str | None:
    """Read and validate the first-message auth payload.

    Returns the ticket string, or ``None`` after closing the socket.
    The timeout is read once per connection from
    ``app_state.ws_auth_timeout_seconds``, which is baked in at
    startup by ``_apply_bridge_config`` from the operator-tunable
    ``api.ws_auth_timeout_seconds`` setting.
    """
    app_state = socket.app.state["app_state"]
    try:
        data = await asyncio.wait_for(
            socket.receive_text(),
            timeout=app_state.ws_auth_timeout_seconds,
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
    socket: WebSocket[Any, Any, Any],
) -> AuthenticatedUser | None:
    """Authenticate via the first message after accept.

    Expects ``{"action": "auth", "ticket": "<ticket>"}``.  Returns
    ``None`` and closes the socket on invalid ticket, wrong message
    format, or timeout.
    """
    ticket = await _read_auth_message(socket)
    if ticket is None:
        return None

    app_state = socket.app.state["app_state"]
    user: AuthenticatedUser | None = app_state.ticket_store.validate_and_consume(
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
    socket: WebSocket[Any, Any, Any],
    user: AuthenticatedUser,
) -> bool:
    """Verify the user has a role permitted for WebSocket access.

    Returns ``True`` if the role is valid.  On failure, closes the
    socket with a forbidden code and returns ``False``.
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


async def _on_event(
    event_data: bytes,
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    queue: asyncio.Queue[bytes],
    conn_user: AuthenticatedUser,
) -> None:
    """Filter a channel event and enqueue it for the outbound consumer.

    Applies the same subscription + access-control + filter checks as
    before, then additionally enforces ``_MAX_OUTBOUND_EVENT_BYTES`` and
    per-client backpressure. Events that pass all checks are enqueued
    onto the client's bounded outbound queue. Oversized events are
    dropped with ``API_WS_EVENT_DROPPED``; events that arrive while the
    queue is full are dropped with ``API_WS_BACKPRESSURE_DROPPED``. In
    neither case is the socket closed -- a single slow consumer or one
    oversized emitter must not nuke the channel for everyone.
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
    except asyncio.QueueFull:
        logger.warning(
            API_WS_BACKPRESSURE_DROPPED,
            channel=channel,
            event_type=str(event_type),
            queue_depth=queue.qsize(),
            max_depth=_OUTBOUND_QUEUE_DEPTH,
        )


async def _outbound_consumer(
    socket: WebSocket[Any, Any, Any],
    queue: asyncio.Queue[bytes],
) -> None:
    """Drain the per-client outbound queue and forward to the socket.

    Runs for the lifetime of the connection. ``WebSocketDisconnect`` is
    treated as a normal shutdown. Any other transport failure closes
    the socket with code 1011 and exits; the surrounding
    ``run_in_background`` context tears the subscription down.
    """
    while True:
        event_data = await queue.get()
        try:
            try:
                await socket.send_text(event_data.decode("utf-8"))
            except WebSocketDisconnect:
                logger.debug(API_WS_SEND_FAILED, reason="client_disconnected")
                return
            except Exception:
                logger.error(API_WS_SEND_FAILED, exc_info=True)
                await socket.close(code=1011, reason="Internal error")
                return
        finally:
            queue.task_done()


async def _send_auth_ok(socket: WebSocket[Any, Any, Any]) -> None:
    """Send the ``auth_ok`` acknowledgement after ticket validation.

    Closes the client-side auth-state flash: clients SHOULD only set
    ``connected=true`` once this message arrives. Transport failures
    (clean disconnect or an unexpected ``send_text`` error) are
    treated as fatal: the socket is closed with 1011 on the generic
    path, mirroring ``_outbound_consumer``'s failure handling, and
    the exception is re-raised so the outer handler runs its cleanup.
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
            exc_info=True,
        )
        await socket.close(code=1011, reason="Internal error")
        raise
    # auth_ok is the handshake state transition: "authenticated" ->
    # "ready to serve". Logging rules require state transitions at
    # INFO so operational dashboards can see the connection lifecycle
    # without turning on DEBUG-level noise.
    logger.info(API_WS_AUTH_OK, client=str(socket.client))


async def _authenticate_ws(
    socket: WebSocket[Any, Any, Any],
) -> tuple[AuthenticatedUser, bool] | None:
    """Run the two-path auth flow.

    Returns ``(user, already_accepted)`` on success, or ``None``
    (socket already closed) on failure.
    """
    ticket_param = socket.query_params.get("ticket")

    if ticket_param is not None:
        user = await _validate_ticket(socket)
        if user is None:
            return None
        return user, False

    # First-message path: must accept before reading
    await socket.accept()
    user = await _auth_from_first_message(socket)
    if user is None:
        return None
    return user, True


def _resolve_channels_plugin(
    socket: WebSocket[Any, Any, Any],
) -> ChannelsPlugin | None:
    """Resolve the ChannelsPlugin from app.plugins.

    Litestar's DI does not reliably inject plugin instances into
    WebSocket handlers (the parameter is misidentified as a query
    param, causing a Litestar-internal 4500 close before the
    handler runs).  See #549.
    """
    for plugin in socket.app.plugins:
        if isinstance(plugin, ChannelsPlugin):
            return plugin
    return None


async def _setup_connection(
    socket: WebSocket[Any, Any, Any],
    user: AuthenticatedUser,
    *,
    already_accepted: bool,
) -> tuple[ChannelsPlugin, Any] | None:
    """Resolve plugin, accept the connection, and subscribe to channels.

    Returns ``(channels_plugin, subscriber)`` on success, or ``None``
    (socket already closed) on failure.

    Note: the first-message auth path calls ``accept()`` before role
    checking.  A valid-ticket, insufficient-role client receives a WS
    upgrade followed immediately by close code 4003.  This is inherent
    to reading over an established WS connection.
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
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="subscribe_failed",
            client=str(socket.client),
            user_id=user.user_id,
            exc_info=True,
        )
        await socket.close(code=1011, reason="Internal error")
        return None

    # Track presence. If the presence registry throws (e.g., the
    # AppState slot is swapped mid-connection), we need to undo the
    # earlier ``subscribe()`` so the subscriber isn't left live for a
    # connection that never finished establishing.
    app_state = socket.app.state["app_state"]
    try:
        app_state.user_presence.connect(user.user_id)
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="presence_connect_failed",
            client=str(socket.client),
            user_id=user.user_id,
            exc_info=True,
        )
        try:
            await channels_plugin.unsubscribe(subscriber)
        except Exception:
            logger.error(
                API_WS_TRANSPORT_ERROR,
                reason="unsubscribe_after_presence_connect_failure",
                client=str(socket.client),
                exc_info=True,
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
    except Exception:
        try:
            await channels_plugin.unsubscribe(subscriber)
        except Exception:
            logger.error(
                API_WS_TRANSPORT_ERROR,
                reason="unsubscribe_after_auth_ok_failure",
                client=str(socket.client),
                exc_info=True,
            )
        app_state.user_presence.disconnect(user.user_id)
        return None

    logger.info(
        API_WS_CONNECTED,
        client=str(socket.client),
        user_id=user.user_id,
    )
    return channels_plugin, subscriber


async def _teardown_connection(
    socket: WebSocket[Any, Any, Any],
    user: AuthenticatedUser,
    channels_plugin: ChannelsPlugin,
    subscriber: Any,
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
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            reason="outbound_consumer_failed",
            client=str(socket.client),
            exc_info=True,
        )
    try:
        await channels_plugin.unsubscribe(subscriber)
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            error="Failed to unsubscribe",
            client=str(socket.client),
            exc_info=True,
        )
    app_state = socket.app.state["app_state"]
    # Presence disconnect is best-effort: if the user_presence
    # registry has been swapped or raises on teardown, we still need
    # to emit ``API_WS_DISCONNECTED`` and re-raise any deferred
    # cancellation so the outer scheduler unwinds cleanly.
    try:
        app_state.user_presence.disconnect(user.user_id)
    except Exception:
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="presence_disconnect_failed",
            client=str(socket.client),
            user_id=user.user_id,
            exc_info=True,
        )
    logger.info(API_WS_DISCONNECTED, client=str(socket.client))
    if outer_cancelled_exc is not None:
        raise outer_cancelled_exc


# Defense-in-depth: opt signals Litestar's auth middleware to skip
# this handler.  The middleware is already HTTP-only (ScopeType.HTTP)
# and the WS path is regex-excluded, so this is a tertiary safeguard.
@websocket("/ws", opt={"exclude_from_auth": True})
async def ws_handler(
    socket: WebSocket[Any, Any, Any],
) -> None:
    """Handle WebSocket connections with channel subscriptions.

    Supports two authentication methods (backward compatible):

    1. **First-message auth** (preferred): connect without ``?ticket``,
       accept the upgrade, then send ``{"action": "auth", "ticket": "..."}``
       as the first message.  Keeps the ticket out of URLs and logs.

    2. **Query-param auth** (legacy): connect with ``?ticket=<ticket>``.
       Validated and consumed before ``accept()``.
    """
    auth_result = await _authenticate_ws(socket)
    if auth_result is None:
        return
    user, already_accepted = auth_result

    if not await _check_ws_role(socket, user):
        return

    setup = await _setup_connection(socket, user, already_accepted=already_accepted)
    if setup is None:
        return
    channels_plugin, subscriber = setup

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

    async def _event_callback(event_data: bytes) -> None:
        await _on_event(
            event_data,
            subscribed,
            filters,
            outbound_queue,
            user,
        )

    consumer_task = asyncio.create_task(
        _outbound_consumer(socket, outbound_queue),
    )
    revalidate_task = asyncio.create_task(_periodic_revalidate(socket, user))
    try:
        async with subscriber.run_in_background(_event_callback):
            await _receive_loop(
                socket,
                subscribed,
                filters,
                user,
                outbound_queue,
            )
    finally:
        revalidate_task.cancel()
        # Cancellation expected; swallow any unrelated raise so
        # teardown is never blocked by revalidate cleanup.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await revalidate_task
        await _teardown_connection(
            socket,
            user,
            channels_plugin,
            subscriber,
            consumer_task,
        )


async def _receive_loop(
    socket: WebSocket[Any, Any, Any],
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    conn_user: AuthenticatedUser,
    outbound_queue: asyncio.Queue[bytes],
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
    """
    try:
        while True:
            data = await socket.receive_text()
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
    except WebSocketDisconnect:
        logger.debug(API_WS_DISCONNECTED, reason="client_disconnect")
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            user_id=conn_user.user_id,
            client=str(socket.client),
            exc_info=True,
        )
        raise
