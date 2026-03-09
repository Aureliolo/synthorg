"""Message bus → Litestar channels bridge.

Polls internal ``MessageBus`` channels and publishes events
to Litestar's ``ChannelsPlugin`` for WebSocket delivery.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from litestar.channels import ChannelsPlugin  # noqa: TC002

from ai_company.api.channels import ALL_CHANNELS
from ai_company.api.ws_models import WsEvent, WsEventType
from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.communication.message import Message  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_STARTUP,
)

logger = get_logger(__name__)

_SUBSCRIBER_ID: str = "__api_bridge__"
_POLL_TIMEOUT: float = 1.0


class MessageBusBridge:
    """Bridge between internal ``MessageBus`` and Litestar channels.

    Subscribes to each internal message bus channel as
    ``__api_bridge__`` and re-publishes messages as ``WsEvent``
    JSON to the corresponding Litestar channel.

    Args:
        message_bus: The internal message bus to poll.
        channels_plugin: The Litestar channels plugin to publish to.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        channels_plugin: ChannelsPlugin,
    ) -> None:
        self._bus = message_bus
        self._plugin = channels_plugin
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start polling tasks for each channel."""
        logger.info(API_APP_STARTUP, component="bus_bridge")
        for channel_name in ALL_CHANNELS:
            try:
                await self._bus.subscribe(channel_name, _SUBSCRIBER_ID)
            except Exception:
                logger.debug(
                    "bus_bridge.subscribe.skip",
                    channel=channel_name,
                )
            task = asyncio.create_task(
                self._poll_channel(channel_name),
                name=f"bridge-{channel_name}",
            )
            self._tasks.append(task)

    async def stop(self) -> None:
        """Cancel all polling tasks."""
        logger.info(API_APP_SHUTDOWN, component="bus_bridge")
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _poll_channel(self, channel_name: str) -> None:
        """Poll a single channel and publish to Litestar."""
        while True:
            try:
                envelope = await self._bus.receive(
                    channel_name,
                    _SUBSCRIBER_ID,
                    timeout=_POLL_TIMEOUT,
                )
                if envelope is None:
                    continue
                ws_event = self._to_ws_event(envelope.message, channel_name)
                self._plugin.publish(
                    ws_event.model_dump_json(),
                    channels=[channel_name],
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug(
                    "bus_bridge.poll.error",
                    channel=channel_name,
                    exc_info=True,
                )
                await asyncio.sleep(_POLL_TIMEOUT)

    @staticmethod
    def _to_ws_event(message: Message, channel_name: str) -> WsEvent:
        """Convert an internal ``Message`` to a ``WsEvent``."""
        payload: dict[str, Any] = {
            "message_id": str(message.id),
            "sender": message.sender,
            "to": message.to,
            "content": message.content,
        }
        return WsEvent(
            event_type=WsEventType.MESSAGE_SENT,
            channel=channel_name,
            timestamp=datetime.now(UTC),
            payload=payload,
        )
