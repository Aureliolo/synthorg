"""WebSocket handler for real-time event feeds.

Clients connect to ``/api/v1/ws`` and send JSON messages to
subscribe/unsubscribe from named channels with optional payload
filters.  The server pushes ``WsEvent`` JSON on subscribed channels.
"""

import json
from typing import Any

from litestar import WebSocket  # noqa: TC002
from litestar.channels import ChannelsPlugin  # noqa: TC002
from litestar.exceptions import WebSocketDisconnect
from litestar.handlers import websocket

from synthorg.api.channels import ALL_CHANNELS
from synthorg.api.guards import require_read_access
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_WS_CONNECTED,
    API_WS_DISCONNECTED,
    API_WS_INVALID_MESSAGE,
    API_WS_SEND_FAILED,
    API_WS_SUBSCRIBE,
    API_WS_TRANSPORT_ERROR,
    API_WS_UNKNOWN_ACTION,
    API_WS_UNSUBSCRIBE,
)

logger = get_logger(__name__)

_ALL_CHANNELS_SET: frozenset[str] = frozenset(ALL_CHANNELS)
_MAX_FILTER_KEYS: int = 10
_MAX_FILTER_VALUE_LEN: int = 256
_MAX_WS_MESSAGE_BYTES: int = 4096


@websocket("/ws", guards=[require_read_access])
async def ws_handler(
    socket: WebSocket[Any, Any, Any],
    channels_plugin: ChannelsPlugin,
) -> None:
    """Handle WebSocket connections with channel subscriptions.

    Clients subscribe to named channels with optional payload
    filters.  The server pushes ``WsEvent`` JSON for matching
    events only.

    Protocol (JSON from client):
        ``{"action": "subscribe", "channels": ["tasks"],
          "filters": {"agent_id": "...", "project": "..."}}``
        ``{"action": "unsubscribe", "channels": ["tasks"]}``
    """
    await socket.accept()
    logger.info(API_WS_CONNECTED, client=str(socket.client))

    subscribed: set[str] = set()
    filters: dict[str, dict[str, str]] = {}

    subscriber = await channels_plugin.subscribe(list(ALL_CHANNELS))

    async def _on_event(event_data: bytes) -> None:
        """Filter and forward events to the client."""
        try:
            event = json.loads(event_data)
        except json.JSONDecodeError, TypeError:
            logger.warning(
                API_WS_INVALID_MESSAGE,
                data_preview=str(event_data)[:100],
                source="channels_backend",
            )
            return

        if not isinstance(event, dict):
            logger.warning(
                API_WS_INVALID_MESSAGE,
                data_preview=str(event_data)[:100],
                reason="not_a_dict",
            )
            return

        channel = event.get("channel", "")
        if channel not in subscribed:
            return

        channel_filters = filters.get(channel)
        if channel_filters:
            payload = event.get("payload", {})
            if not all(payload.get(k) == v for k, v in channel_filters.items()):
                return

        try:
            await socket.send_text(event_data.decode("utf-8"))
        except WebSocketDisconnect:
            logger.debug(API_WS_SEND_FAILED, reason="client_disconnected")
        except Exception:
            logger.warning(
                API_WS_SEND_FAILED,
                exc_info=True,
            )

    try:
        async with subscriber.run_in_background(_on_event):
            await _receive_loop(socket, subscribed, filters)
    finally:
        await channels_plugin.unsubscribe(subscriber)
        logger.info(API_WS_DISCONNECTED, client=str(socket.client))


async def _receive_loop(
    socket: WebSocket[Any, Any, Any],
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
) -> None:
    """Process client subscribe/unsubscribe commands."""
    try:
        while True:
            data = await socket.receive_text()
            response = _handle_message(data, subscribed, filters)
            await socket.send_text(response)
    except WebSocketDisconnect:
        logger.debug(API_WS_DISCONNECTED, reason="client_disconnect")
    except Exception:
        logger.error(
            API_WS_TRANSPORT_ERROR,
            exc_info=True,
        )
        raise


def _handle_message(  # noqa: C901, PLR0911
    data: str,
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
) -> str:
    """Parse and handle a single client message.

    Args:
        data: Raw JSON string from the client.
        subscribed: Mutable set of subscribed channel names.
        filters: Mutable per-channel payload filters.

    Returns:
        JSON acknowledgement or error string.
    """
    if len(data.encode()) > _MAX_WS_MESSAGE_BYTES:
        return json.dumps({"error": "Message too large"})

    try:
        msg = json.loads(data)
    except json.JSONDecodeError, TypeError:
        logger.warning(
            API_WS_INVALID_MESSAGE,
            data_preview=str(data)[:100],
        )
        return json.dumps({"error": "Invalid JSON"})

    if not isinstance(msg, dict):
        return json.dumps({"error": "Expected JSON object"})

    action = msg.get("action")
    channels = msg.get("channels", [])
    client_filters = msg.get("filters", {})

    if not isinstance(channels, list) or not all(isinstance(c, str) for c in channels):
        return json.dumps({"error": "channels must be a list of strings"})
    if not isinstance(client_filters, dict):
        return json.dumps({"error": "filters must be an object"})

    if action == "subscribe":
        # Validate filter bounds to prevent memory abuse.
        if len(client_filters) > _MAX_FILTER_KEYS or any(
            len(str(v)) > _MAX_FILTER_VALUE_LEN for v in client_filters.values()
        ):
            return json.dumps({"error": "Filter bounds exceeded"})

        valid = [c for c in channels if c in _ALL_CHANNELS_SET]
        subscribed.update(valid)
        for c in valid:
            if client_filters:
                filters[c] = dict(client_filters)
        logger.debug(
            API_WS_SUBSCRIBE,
            channels=valid,
            active=sorted(subscribed),
        )
        return json.dumps({"action": "subscribed", "channels": sorted(subscribed)})

    if action == "unsubscribe":
        subscribed -= set(channels)
        for c in channels:
            filters.pop(c, None)
        logger.debug(
            API_WS_UNSUBSCRIBE,
            channels=channels,
            active=sorted(subscribed),
        )
        return json.dumps({"action": "unsubscribed", "channels": sorted(subscribed)})

    logger.warning(API_WS_UNKNOWN_ACTION, action=str(action)[:64])
    return json.dumps({"error": "Unknown action"})
