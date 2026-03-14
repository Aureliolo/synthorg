"""Tests for message bus bridge."""

from datetime import UTC, datetime

import pytest

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.ws_models import WsEventType
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message


@pytest.mark.unit
class TestMessageBusBridge:
    def test_to_ws_event_conversion(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "content": "Hello!",
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "messages")
        assert event.event_type == WsEventType.MESSAGE_SENT
        assert event.channel == "messages"
        assert event.payload["sender"] == "alice"
        assert event.payload["content"] == "Hello!"

    def test_to_ws_event_has_timestamp(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "content": "Test",
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "tasks")
        assert event.timestamp is not None


@pytest.mark.unit
class TestBridgeLifecycle:
    async def test_start_creates_tasks(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        assert bridge._running is True
        assert len(bridge._tasks) > 0
        await bridge.stop()

    async def test_double_start_raises(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        with pytest.raises(RuntimeError, match="already running"):
            await bridge.start()
        await bridge.stop()

    async def test_stop_cancels_tasks(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        await bridge.stop()
        assert bridge._running is False
        assert len(bridge._tasks) == 0

    async def test_start_zero_channels_raises(self) -> None:
        """If all subscriptions fail, bridge should raise."""
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()

        # Make subscribe always fail
        async def failing_subscribe(channel_name: str, subscriber_id: str) -> None:
            msg = "sub fail"
            raise OSError(msg)

        bus.subscribe = failing_subscribe  # type: ignore[method-assign]

        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        with pytest.raises(RuntimeError, match="failed to subscribe"):
            await bridge.start()


@pytest.mark.unit
class TestPollChannel:
    async def test_circuit_breaker_after_max_errors(self) -> None:
        """Polling stops after _MAX_CONSECUTIVE_ERRORS failures."""
        from unittest.mock import patch

        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.bus_bridge import _MAX_CONSECUTIVE_ERRORS
        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()

        call_count = 0

        async def failing_receive(
            channel_name: str,
            subscriber_id: str,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> None:
            nonlocal call_count
            call_count += 1
            msg = "connection lost"
            raise OSError(msg)

        bus.receive = failing_receive  # type: ignore[method-assign]

        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        # Patch _POLL_TIMEOUT to 0 so sleeps between errors are instant
        with patch("synthorg.api.bus_bridge._POLL_TIMEOUT", 0.0):
            await bridge._poll_channel("tasks")
        assert call_count >= _MAX_CONSECUTIVE_ERRORS
