"""Unit tests for InMemoryMessageBus."""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.communication.bus_memory import InMemoryMessageBus
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.channel import Channel
from synthorg.communication.config import (
    MessageBusConfig,
    MessageRetentionConfig,
)
from synthorg.communication.enums import (
    ChannelType,
    MessageType,
)
from synthorg.communication.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundError,
    MessageBusAlreadyRunningError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from synthorg.communication.message import Message

pytestmark = pytest.mark.timeout(30)


def _make_message(
    *,
    sender: str = "agent-a",
    to: str = "agent-b",
    channel: str = "#test",
    content: str = "test content",
) -> Message:
    """Create a test message with sensible defaults."""
    return Message(
        timestamp=datetime.now(UTC),
        sender=sender,
        to=to,
        type=MessageType.TASK_UPDATE,
        channel=channel,
        content=content,
    )


def _make_config(
    *,
    channels: tuple[str, ...] = ("#general",),
    max_messages: int = 1000,
) -> MessageBusConfig:
    """Create a test bus config."""
    return MessageBusConfig(
        channels=channels,
        retention=MessageRetentionConfig(
            max_messages_per_channel=max_messages,
        ),
    )


# ── Lifecycle ─────────────────────────────────────────────────────


class TestBusLifecycle:
    """Tests for start/stop/is_running lifecycle."""

    @pytest.mark.unit
    async def test_not_running_initially(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        assert bus.is_running is False

    @pytest.mark.unit
    async def test_start_sets_running(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        assert bus.is_running is True

    @pytest.mark.unit
    async def test_stop_clears_running(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.stop()
        assert bus.is_running is False

    @pytest.mark.unit
    async def test_double_start_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(MessageBusAlreadyRunningError):
            await bus.start()

    @pytest.mark.unit
    async def test_stop_is_idempotent(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.stop()
        await bus.stop()
        assert bus.is_running is False

    @pytest.mark.unit
    async def test_publish_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        msg = _make_message(channel="#general")
        with pytest.raises(MessageBusNotRunningError):
            await bus.publish(msg)

    @pytest.mark.unit
    async def test_subscribe_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        with pytest.raises(MessageBusNotRunningError):
            await bus.subscribe("#general", "agent-a")

    @pytest.mark.unit
    async def test_create_channel_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        ch = Channel(name="#new", type=ChannelType.TOPIC)
        with pytest.raises(MessageBusNotRunningError):
            await bus.create_channel(ch)

    @pytest.mark.unit
    async def test_start_creates_configured_channels(self) -> None:
        bus = InMemoryMessageBus(
            config=_make_config(channels=("#alpha", "#beta")),
        )
        await bus.start()
        channels = await bus.list_channels()
        names = {ch.name for ch in channels}
        assert names == {"#alpha", "#beta"}

    @pytest.mark.unit
    async def test_send_direct_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        msg = _make_message(sender="agent-a", to="agent-b")
        with pytest.raises(MessageBusNotRunningError):
            await bus.send_direct(msg, recipient="agent-b")

    @pytest.mark.unit
    async def test_unsubscribe_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        with pytest.raises(MessageBusNotRunningError):
            await bus.unsubscribe("#general", "agent-a")


# ── Protocol Conformance ─────────────────────────────────────


class TestProtocolConformance:
    """Tests that InMemoryMessageBus satisfies the MessageBus protocol."""

    @pytest.mark.unit
    def test_isinstance_message_bus(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        assert isinstance(bus, MessageBus)


# ── Channel Management ───────────────────────────────────────────


class TestChannelManagement:
    """Tests for create/get/list channels."""

    @pytest.mark.unit
    async def test_create_channel(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        ch = Channel(name="#new-channel", type=ChannelType.TOPIC)
        created = await bus.create_channel(ch)
        assert created.name == "#new-channel"
        assert created.type == ChannelType.TOPIC

    @pytest.mark.unit
    async def test_create_duplicate_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        ch = Channel(name="#general", type=ChannelType.TOPIC)
        with pytest.raises(ChannelAlreadyExistsError):
            await bus.create_channel(ch)

    @pytest.mark.unit
    async def test_get_channel(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        ch = await bus.get_channel("#general")
        assert ch.name == "#general"
        assert ch.type == ChannelType.TOPIC

    @pytest.mark.unit
    async def test_get_missing_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(ChannelNotFoundError):
            await bus.get_channel("#nonexistent")

    @pytest.mark.unit
    async def test_list_channels_returns_tuple(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        channels = await bus.list_channels()
        assert isinstance(channels, tuple)


# ── Subscription ──────────────────────────────────────────────────


class TestSubscription:
    """Tests for subscribe/unsubscribe."""

    @pytest.mark.unit
    async def test_subscribe_returns_subscription(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        sub = await bus.subscribe("#general", "agent-a")
        assert sub.channel_name == "#general"
        assert sub.subscriber_id == "agent-a"

    @pytest.mark.unit
    async def test_subscribe_adds_to_channel_subscribers(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        ch = await bus.get_channel("#general")
        assert "agent-a" in ch.subscribers

    @pytest.mark.unit
    async def test_subscribe_to_missing_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(ChannelNotFoundError):
            await bus.subscribe("#nonexistent", "agent-a")

    @pytest.mark.unit
    async def test_idempotent_subscribe(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        sub1 = await bus.subscribe("#general", "agent-a")
        sub2 = await bus.subscribe("#general", "agent-a")
        assert sub1.channel_name == sub2.channel_name
        assert sub1.subscriber_id == sub2.subscriber_id
        ch = await bus.get_channel("#general")
        count = sum(1 for s in ch.subscribers if s == "agent-a")
        assert count == 1

    @pytest.mark.unit
    async def test_unsubscribe_removes_subscriber(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        await bus.unsubscribe("#general", "agent-a")
        ch = await bus.get_channel("#general")
        assert "agent-a" not in ch.subscribers

    @pytest.mark.unit
    async def test_unsubscribe_not_subscribed_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(NotSubscribedError):
            await bus.unsubscribe("#general", "agent-a")

    @pytest.mark.unit
    async def test_unsubscribe_nonexistent_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(NotSubscribedError):
            await bus.unsubscribe("#nonexistent", "agent-a")

    @pytest.mark.unit
    async def test_unsubscribe_wakes_blocked_receive(self) -> None:
        """Unsubscribing wakes a blocked receive(), which returns None."""
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")

        received: list[object] = []

        async def receiver() -> None:
            result = await bus.receive("#general", "agent-a")
            received.append(result)

        async def unsubscriber() -> None:
            await asyncio.sleep(0.05)
            await bus.unsubscribe("#general", "agent-a")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(receiver())
            tg.create_task(unsubscriber())

        assert received == [None]

    @pytest.mark.unit
    async def test_unsubscribe_wakes_multiple_blocked_receivers(self) -> None:
        """Unsubscribing wakes all concurrent blocked receive() calls."""
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")

        received: list[object] = []

        async def receiver() -> None:
            result = await bus.receive("#general", "agent-a")
            received.append(result)

        async def unsubscriber() -> None:
            await asyncio.sleep(0.05)
            await bus.unsubscribe("#general", "agent-a")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(receiver())
            tg.create_task(receiver())
            tg.create_task(receiver())
            tg.create_task(unsubscriber())

        assert len(received) == 3
        assert all(r is None for r in received)


# ── Publish & Receive ────────────────────────────────────────────


class TestPublishReceive:
    """Tests for publish/receive message flow."""

    @pytest.mark.unit
    async def test_publish_and_receive(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        msg = _make_message(channel="#general")
        await bus.publish(msg)
        envelope = await bus.receive("#general", "agent-a", timeout=1.0)
        assert envelope is not None
        assert envelope.message.content == "test content"
        assert envelope.channel_name == "#general"

    @pytest.mark.unit
    async def test_publish_to_missing_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(channel="#nonexistent")
        with pytest.raises(ChannelNotFoundError):
            await bus.publish(msg)

    @pytest.mark.unit
    async def test_fifo_ordering(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        for i in range(5):
            msg = _make_message(channel="#general", content=f"msg-{i}")
            await bus.publish(msg)
        for i in range(5):
            envelope = await bus.receive(
                "#general",
                "agent-a",
                timeout=1.0,
            )
            assert envelope is not None
            assert envelope.message.content == f"msg-{i}"

    @pytest.mark.unit
    async def test_fan_out_to_multiple_subscribers(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        await bus.subscribe("#general", "agent-b")
        msg = _make_message(channel="#general")
        await bus.publish(msg)
        env_a = await bus.receive("#general", "agent-a", timeout=1.0)
        env_b = await bus.receive("#general", "agent-b", timeout=1.0)
        assert env_a is not None
        assert env_b is not None
        assert env_a.message.id == env_b.message.id

    @pytest.mark.unit
    async def test_receive_timeout_returns_none(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        envelope = await bus.receive("#general", "agent-a", timeout=0.05)
        assert envelope is None

    @pytest.mark.unit
    async def test_receive_without_timeout_blocks_until_message(self) -> None:
        """receive() with timeout=None blocks until a message arrives."""
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")

        received: list[str] = []

        async def receiver() -> None:
            envelope = await bus.receive("#general", "agent-a")
            assert envelope is not None
            received.append(envelope.message.content)

        async def publisher() -> None:
            await asyncio.sleep(0.05)
            msg = _make_message(channel="#general", content="delayed")
            await bus.publish(msg)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(receiver())
            tg.create_task(publisher())

        assert received == ["delayed"]

    @pytest.mark.unit
    async def test_broadcast_delivers_to_all_known_agents(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        broadcast_ch = Channel(
            name="#announcements",
            type=ChannelType.BROADCAST,
        )
        await bus.create_channel(broadcast_ch)
        # Subscribe agents to a different channel to register them
        await bus.subscribe("#general", "agent-a")
        await bus.subscribe("#general", "agent-b")
        await bus.subscribe("#general", "agent-c")
        msg = _make_message(channel="#announcements")
        await bus.publish(msg)
        for agent_id in ("agent-a", "agent-b", "agent-c"):
            envelope = await bus.receive(
                "#announcements",
                agent_id,
                timeout=1.0,
            )
            assert envelope is not None
            assert envelope.message.id == msg.id


# ── Direct Messaging ─────────────────────────────────────────────


class TestDirectMessaging:
    """Tests for send_direct."""

    @pytest.mark.unit
    async def test_direct_creates_channel_lazily(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(sender="agent-a", to="agent-b")
        await bus.send_direct(msg, recipient="agent-b")
        ch = await bus.get_channel("@agent-a:agent-b")
        assert ch.type == ChannelType.DIRECT

    @pytest.mark.unit
    async def test_direct_deterministic_channel_name(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg1 = _make_message(sender="agent-b", to="agent-a")
        await bus.send_direct(msg1, recipient="agent-a")
        msg2 = _make_message(sender="agent-a", to="agent-b")
        await bus.send_direct(msg2, recipient="agent-b")
        # Both should use the same sorted channel name
        ch = await bus.get_channel("@agent-a:agent-b")
        assert ch is not None

    @pytest.mark.unit
    async def test_direct_both_receive(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(sender="agent-a", to="agent-b")
        await bus.send_direct(msg, recipient="agent-b")
        ch_name = "@agent-a:agent-b"
        env_a = await bus.receive(ch_name, "agent-a", timeout=1.0)
        env_b = await bus.receive(ch_name, "agent-b", timeout=1.0)
        assert env_a is not None
        assert env_b is not None
        assert env_a.message.id == env_b.message.id


# ── Retention ─────────────────────────────────────────────────────


class TestRetention:
    """Tests for message retention limits."""

    @pytest.mark.unit
    async def test_history_respects_max_messages(self) -> None:
        bus = InMemoryMessageBus(
            config=_make_config(max_messages=3),
        )
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        for i in range(5):
            msg = _make_message(
                channel="#general",
                content=f"msg-{i}",
            )
            await bus.publish(msg)
        history = await bus.get_channel_history("#general")
        assert len(history) == 3
        # Only the last 3 should remain
        assert history[0].content == "msg-2"
        assert history[1].content == "msg-3"
        assert history[2].content == "msg-4"


# ── History ───────────────────────────────────────────────────────


class TestHistory:
    """Tests for get_channel_history."""

    @pytest.mark.unit
    async def test_history_returns_all_messages(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        for i in range(3):
            msg = _make_message(
                channel="#general",
                content=f"msg-{i}",
            )
            await bus.publish(msg)
        history = await bus.get_channel_history("#general")
        assert len(history) == 3

    @pytest.mark.unit
    async def test_history_with_limit(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        for i in range(5):
            msg = _make_message(
                channel="#general",
                content=f"msg-{i}",
            )
            await bus.publish(msg)
        history = await bus.get_channel_history("#general", limit=2)
        assert len(history) == 2
        # Should return the most recent 2
        assert history[0].content == "msg-3"
        assert history[1].content == "msg-4"

    @pytest.mark.unit
    async def test_history_empty_channel(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        history = await bus.get_channel_history("#general")
        assert history == ()

    @pytest.mark.unit
    async def test_history_missing_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(ChannelNotFoundError):
            await bus.get_channel_history("#nonexistent")

    @pytest.mark.unit
    async def test_history_for_direct_message_channel(self) -> None:
        """Direct message channels also support history queries."""
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(sender="agent-a", to="agent-b", content="dm-1")
        await bus.send_direct(msg, recipient="agent-b")
        history = await bus.get_channel_history("@agent-a:agent-b")
        assert len(history) == 1
        assert history[0].content == "dm-1"


# ── Concurrency ───────────────────────────────────────────────────


class TestConcurrency:
    """Tests for concurrent publish/receive operations."""

    @pytest.mark.unit
    async def test_concurrent_publish_and_receive(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        n = 20

        async def publisher() -> None:
            for i in range(n):
                msg = _make_message(
                    channel="#general",
                    content=f"msg-{i}",
                )
                await bus.publish(msg)

        async def consumer() -> list[str]:
            received: list[str] = []
            while len(received) < n:
                env = await bus.receive(
                    "#general",
                    "agent-a",
                    timeout=2.0,
                )
                if env is not None:
                    received.append(env.message.content)
            return received

        async with asyncio.TaskGroup() as tg:
            tg.create_task(publisher())
            consumer_task = tg.create_task(consumer())

        result = consumer_task.result()
        assert len(result) == n

    @pytest.mark.unit
    async def test_concurrent_multiple_publishers(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "reader")
        msgs_per_publisher = 10
        num_publishers = 3

        async def publisher(prefix: str) -> None:
            for i in range(msgs_per_publisher):
                msg = _make_message(
                    channel="#general",
                    sender=prefix,
                    content=f"{prefix}-{i}",
                )
                await bus.publish(msg)

        async def consumer() -> list[str]:
            received: list[str] = []
            total = msgs_per_publisher * num_publishers
            while len(received) < total:
                env = await bus.receive(
                    "#general",
                    "reader",
                    timeout=2.0,
                )
                if env is not None:
                    received.append(env.message.content)
            return received

        async with asyncio.TaskGroup() as tg:
            for p in range(num_publishers):
                tg.create_task(publisher(f"pub-{p}"))
            consumer_task = tg.create_task(consumer())

        result = consumer_task.result()
        assert len(result) == msgs_per_publisher * num_publishers


# ── Receive Validation ─────────────────────────────────────────────


class TestReceiveValidation:
    """Tests for receive() running, channel, and subscription checks."""

    @pytest.mark.unit
    async def test_receive_on_stopped_bus_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        with pytest.raises(MessageBusNotRunningError):
            await bus.receive("#general", "agent-a", timeout=0.1)

    @pytest.mark.unit
    async def test_receive_nonexistent_channel_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(ChannelNotFoundError):
            await bus.receive("#nonexistent", "agent-a", timeout=0.1)

    @pytest.mark.unit
    async def test_receive_not_subscribed_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        with pytest.raises(NotSubscribedError):
            await bus.receive("#general", "agent-a", timeout=0.1)

    @pytest.mark.unit
    async def test_receive_returns_none_on_shutdown(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")

        async def stop_after_delay() -> None:
            await asyncio.sleep(0.05)
            await bus.stop()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(stop_after_delay())
            result = await bus.receive("#general", "agent-a", timeout=5.0)

        assert result is None


# ── send_direct Validation ─────────────────────────────────────────


class TestSendDirectValidation:
    """Tests for send_direct() recipient and agent ID validation."""

    @pytest.mark.unit
    async def test_send_direct_recipient_mismatch_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(sender="agent-a", to="agent-b")
        with pytest.raises(ValueError, match="does not match"):
            await bus.send_direct(msg, recipient="agent-c")

    @pytest.mark.unit
    async def test_send_direct_colon_in_agent_id_raises(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        msg = _make_message(sender="agent:a", to="agent-b")
        with pytest.raises(ValueError, match="separator character"):
            await bus.send_direct(msg, recipient="agent-b")


# ── History Edge Cases ─────────────────────────────────────────────


class TestHistoryEdgeCases:
    """Tests for get_channel_history limit edge cases."""

    @pytest.mark.unit
    async def test_history_limit_zero_returns_empty(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        await bus.publish(
            _make_message(channel="#general", content="msg-1"),
        )
        history = await bus.get_channel_history("#general", limit=0)
        assert history == ()

    @pytest.mark.unit
    async def test_history_limit_negative_returns_empty(self) -> None:
        bus = InMemoryMessageBus(config=_make_config())
        await bus.start()
        await bus.subscribe("#general", "agent-a")
        await bus.publish(
            _make_message(channel="#general", content="msg-1"),
        )
        history = await bus.get_channel_history("#general", limit=-5)
        assert history == ()
