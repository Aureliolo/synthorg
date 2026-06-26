"""Session-less dashboard SSE stream over the WebSocket channel feed.

The per-task AG-UI stream (`_sse._sse_event_stream`) is keyed by a task
``session_id`` and is unsuitable for the dashboard-wide read-only fallback
the SPA opens when the WebSocket upgrade is proxy-blocked. This module
bridges the same Litestar ``ChannelsPlugin`` feed the WebSocket handler
serves into an SSE stream: it subscribes to the channels the user is
permitted to read, forwards each published ``WsEvent`` as a single named
``ws`` frame (so the client needs one listener, not one per event type),
emits periodic keepalives, and optionally replays the recent per-channel
backlog on reconnect. Auth revalidation is layered on by
``revalidated_sse_stream`` so a revoked session tears the stream down
within one revalidation interval.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Final

from litestar.channels import ChannelsPlugin, Subscriber

from synthorg.api.channels import ALL_CHANNELS, user_channel
from synthorg.api.controllers.events._sse import _resolve_sse_keepalive_seconds
from synthorg.api.controllers.ws_protocol import channel_allowed
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_DASHBOARD_SSE_FRAME_INVALID,
    API_DASHBOARD_SSE_SUBSCRIBED,
    API_DASHBOARD_SSE_UNSUBSCRIBE_FAILED,
    API_DASHBOARD_SSE_UNSUBSCRIBED,
)

logger = get_logger(__name__)

# Recent per-channel events the backend replays on reconnect so a brief
# disconnect does not silently drop updates. Matches the
# ``MemoryChannelsBackend`` ring size.
_DASHBOARD_REPLAY_LIMIT: Final[int] = 20


def resolve_dashboard_channels(user: AuthenticatedUser) -> list[str]:
    """Return the channels a user may subscribe to over the dashboard SSE feed.

    Mirrors the WebSocket handler's grant logic: every shared channel the
    user is permitted to read (``channel_allowed`` gates budget / internal
    channels by role) plus the user's own ``user:{id}`` channel.

    Returns:
        The list of channel names to subscribe to.
    """
    channels = [ch for ch in ALL_CHANNELS if channel_allowed(ch, user)]
    channels.append(user_channel(user.user_id))
    return channels


def _dashboard_frame(data: bytes | str, frame_id: int) -> dict[str, str] | None:
    """Render a published channel message as an SSE ``ws`` frame.

    The channel payload is a ``WsEvent`` JSON document (published via
    ``publish_ws_event``); it is forwarded verbatim under a fixed ``ws``
    event name with a per-connection monotonic id so the browser records a
    ``lastEventId`` for reconnect. Malformed payloads are dropped with a
    WARNING so a misbehaving publisher is observable rather than silently
    starving the dashboard.

    Returns:
        The frame dict, or ``None`` when the payload is not a valid event.
    """
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.warning(
            API_DASHBOARD_SSE_FRAME_INVALID,
            reason="decode_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if (
        not isinstance(obj, dict)
        or not isinstance(obj.get("event_type"), str)
        or not isinstance(obj.get("channel"), str)
        or not isinstance(obj.get("payload"), dict)
    ):
        logger.warning(API_DASHBOARD_SSE_FRAME_INVALID, reason="schema_mismatch")
        return None
    return {"event": "ws", "data": text, "id": str(frame_id)}


async def _stream_dashboard_frames(
    subscriber: Subscriber,
    *,
    keepalive_seconds: float,
    clock: Clock,
) -> AsyncIterator[dict[str, str]]:
    """Yield ``ws`` data frames and ``keepalive`` heartbeats from *subscriber*.

    Races ``anext`` against the keepalive deadline: a published event is
    forwarded as a ``ws`` frame; an elapsed interval with no traffic emits a
    ``keepalive`` frame so intermediary proxies keep the stream open. The
    in-flight ``anext`` task is preserved across keepalives via
    ``asyncio.shield`` and cancelled on teardown.

    Yields:
        SSE frame dicts (``ws`` data frames and ``keepalive`` heartbeats).
    """
    events = subscriber.iter_events()
    pending: asyncio.Task[bytes] | None = None
    frame_id = 0
    try:
        next_keepalive = clock.monotonic() + keepalive_seconds
        # lint-allow: long-running-loop-kill-switch -- per-request SSE stream; lifetime bounded by client connection (CancelledError on disconnect) + auth revocation  # noqa: E501
        while True:
            timeout = max(0.0, next_keepalive - clock.monotonic())
            if pending is None:
                pending = asyncio.create_task(anext(events))
            try:
                data = await asyncio.wait_for(asyncio.shield(pending), timeout)
            except TimeoutError:
                yield {"event": "keepalive", "data": "{}"}
                next_keepalive = clock.monotonic() + keepalive_seconds
                continue
            except StopAsyncIteration:
                return
            pending = None
            frame = _dashboard_frame(data, frame_id)
            if frame is not None:
                frame_id += 1
                yield frame
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


async def dashboard_channel_frames(
    plugin: ChannelsPlugin,
    channels: list[str],
    *,
    app_state: AppState | None = None,
    replay: bool = False,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE frames for the user's dashboard channels.

    Subscribes to ``channels`` (replaying the recent backlog when ``replay``
    is set), forwards each published ``WsEvent`` as a ``ws`` frame, and emits
    a ``keepalive`` frame on each idle keepalive interval. Wrap with
    ``revalidated_sse_stream`` for periodic auth revalidation. The
    subscription is always torn down, including when cancellation arrives
    before the first event.

    Yields:
        SSE frame dicts (``ws`` data frames and ``keepalive`` heartbeats).
    """
    keepalive_seconds = await _resolve_sse_keepalive_seconds(app_state)
    clock = app_state.clock if app_state is not None else SystemClock()
    subscriber: Subscriber | None = None
    try:
        subscriber = await plugin.subscribe(
            channels,
            history=_DASHBOARD_REPLAY_LIMIT if replay else None,
        )
        logger.debug(
            API_DASHBOARD_SSE_SUBSCRIBED,
            channel_count=len(channels),
            replay=replay,
        )
        async for frame in _stream_dashboard_frames(
            subscriber,
            keepalive_seconds=keepalive_seconds,
            clock=clock,
        ):
            yield frame
    finally:
        if subscriber is not None:
            try:
                await plugin.unsubscribe(subscriber)
                logger.debug(
                    API_DASHBOARD_SSE_UNSUBSCRIBED, channel_count=len(channels)
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
                reraise_critical(exc)
                logger.warning(
                    API_DASHBOARD_SSE_UNSUBSCRIBE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
