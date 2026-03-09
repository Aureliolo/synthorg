"""WebSocket handler for real-time event feeds.

Clients connect to ``/api/v1/ws`` and send JSON messages to
subscribe/unsubscribe from named channels. The server pushes
``WsEvent`` JSON on subscribed channels.
"""

import json
from typing import Any

from litestar import WebSocket  # noqa: TC002
from litestar.channels import ChannelsPlugin  # noqa: TC002
from litestar.handlers import WebsocketListener

from ai_company.api.channels import ALL_CHANNELS
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_WS_CONNECTED,
    API_WS_DISCONNECTED,
    API_WS_INVALID_MESSAGE,
    API_WS_SUBSCRIBE,
    API_WS_UNKNOWN_ACTION,
    API_WS_UNSUBSCRIBE,
)

logger = get_logger(__name__)


class WsHandler(WebsocketListener):
    """WebSocket handler for channel subscriptions.

    Litestar's ``WebsocketListener`` creates a new handler instance
    per connection, so ``_subscribed`` is safe per-connection state.

    Protocol (JSON):
    - ``{"action": "subscribe", "channels": ["tasks"]}``
    - ``{"action": "unsubscribe", "channels": ["tasks"]}``

    Server pushes ``WsEvent`` JSON on subscribed channels.
    """

    path = "/ws"

    def __init__(self, channels_plugin: ChannelsPlugin, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._plugin = channels_plugin
        self._subscribed: set[str] = set()

    def on_accept(self, socket: WebSocket[Any, Any, Any]) -> None:
        """Log connection accepted."""
        logger.info(
            API_WS_CONNECTED,
            client=str(socket.client),
        )

    def on_disconnect(self, socket: WebSocket[Any, Any, Any]) -> None:
        """Log disconnection and clean up subscriptions."""
        logger.info(
            API_WS_DISCONNECTED,
            client=str(socket.client),
        )
        self._subscribed.clear()

    def on_receive(self, data: str) -> str | None:
        """Handle subscribe/unsubscribe messages.

        Args:
            data: Raw JSON string from the client.

        Returns:
            JSON acknowledgement or error, or ``None``.
        """
        try:
            msg = json.loads(data)
        except json.JSONDecodeError, TypeError:
            logger.warning(
                API_WS_INVALID_MESSAGE,
                data_preview=str(data)[:100],
            )
            return json.dumps({"error": "Invalid JSON"})

        action = msg.get("action")
        channels = msg.get("channels", [])

        if action == "subscribe":
            valid = [c for c in channels if c in ALL_CHANNELS]
            self._subscribed.update(valid)
            logger.debug(
                API_WS_SUBSCRIBE,
                channels=valid,
                active=sorted(self._subscribed),
            )
            return json.dumps(
                {
                    "action": "subscribed",
                    "channels": sorted(self._subscribed),
                }
            )

        if action == "unsubscribe":
            self._subscribed -= set(channels)
            logger.debug(
                API_WS_UNSUBSCRIBE,
                channels=channels,
                active=sorted(self._subscribed),
            )
            return json.dumps(
                {
                    "action": "unsubscribed",
                    "channels": sorted(self._subscribed),
                }
            )

        logger.warning(API_WS_UNKNOWN_ACTION, action=str(action)[:64])
        return json.dumps({"error": "Unknown action"})
