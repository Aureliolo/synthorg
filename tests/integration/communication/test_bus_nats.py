"""Integration tests for the NATS JetStream message bus backend.

Runs the same protocol-conformance scenarios covered by the in-memory
bus unit tests against a real NATS JetStream container via
testcontainers. Skipped unless Docker is available and the container
can be started.

Mapped to the Distributed Runtime design page:
``docs/design/distributed-runtime.md``.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Final

import pytest

from synthorg.communication.bus import build_message_bus
from synthorg.communication.bus.nats import JetStreamMessageBus
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.channel import Channel
from synthorg.communication.config import (
    MessageBusConfig,
    MessageRetentionConfig,
    NatsConfig,
)
from synthorg.communication.enums import (
    ChannelType,
    MessageBusBackend,
    MessageType,
)
from synthorg.communication.errors import (
    ChannelNotFoundError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from synthorg.communication.message import Message, TextPart

pytestmark = pytest.mark.integration


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
        parts=(TextPart(text=content),),
    )


def _make_config(
    *,
    url: str,
    channels: tuple[str, ...] = ("#general",),
    max_messages: int = 1000,
    stream_prefix: str = "TEST",
) -> MessageBusConfig:
    """Create a NATS-backed bus config pointed at the test container."""
    return MessageBusConfig(
        backend=MessageBusBackend.NATS,
        channels=channels,
        retention=MessageRetentionConfig(
            max_messages_per_channel=max_messages,
        ),
        nats=NatsConfig(
            url=url,
            stream_name_prefix=stream_prefix,
            connect_timeout_seconds=10.0,
            publish_ack_wait_seconds=5.0,
        ),
    )


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    """Start a NATS JetStream container for the module's tests.

    Skips the entire module if Docker or the container cannot be
    started (e.g. CI without Docker, local dev without the daemon
    running).
    """
    try:
        from testcontainers.core.container import DockerContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    # renovate: datasource=docker depName=nats
    container = DockerContainer("nats:2.12.6-alpine")
    container.with_command("-js")
    container.with_exposed_ports(4222)
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"could not start NATS container: {exc}")

    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(4222))
    url = f"nats://{host}:{port}"
    try:
        yield url
    finally:
        container.stop()


@pytest.fixture
async def bus(nats_url: str) -> AsyncIterator[MessageBus]:
    """Yield a started JetStreamMessageBus pointed at the container.

    Uses a unique stream prefix per test to isolate streams so
    parallel runs do not collide on durable consumer names.
    """
    import uuid

    prefix = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    config = _make_config(url=nats_url, stream_prefix=prefix)
    instance = JetStreamMessageBus(config=config)
    await instance.start()
    try:
        yield instance
    finally:
        if instance.is_running:
            await instance.stop()


# -- Lifecycle ---------------------------------------------------------


async def test_start_sets_running(bus: MessageBus) -> None:
    assert bus.is_running is True


async def test_stop_clears_running(bus: MessageBus) -> None:
    await bus.stop()
    assert bus.is_running is False


async def test_stop_is_idempotent(bus: MessageBus) -> None:
    await bus.stop()
    await bus.stop()
    assert bus.is_running is False


async def test_publish_on_stopped_bus_raises(bus: MessageBus) -> None:
    await bus.stop()
    msg = _make_message(channel="#general")
    with pytest.raises(MessageBusNotRunningError):
        await bus.publish(msg)


async def test_start_creates_configured_channels(bus: MessageBus) -> None:
    channels = await bus.list_channels()
    names = {ch.name for ch in channels}
    assert "#general" in names


# -- Channel Management ------------------------------------------------


async def test_get_channel(bus: MessageBus) -> None:
    ch = await bus.get_channel("#general")
    assert ch.name == "#general"
    assert ch.type == ChannelType.TOPIC


async def test_get_missing_channel_raises(bus: MessageBus) -> None:
    with pytest.raises(ChannelNotFoundError):
        await bus.get_channel("#nonexistent")


async def test_create_channel(bus: MessageBus) -> None:
    ch = Channel(name="#new-channel", type=ChannelType.TOPIC)
    created = await bus.create_channel(ch)
    assert created.name == "#new-channel"
    assert created.type == ChannelType.TOPIC


# -- Subscription ------------------------------------------------------


async def test_subscribe_returns_subscription(bus: MessageBus) -> None:
    sub = await bus.subscribe("#general", "agent-a")
    assert sub.channel_name == "#general"
    assert sub.subscriber_id == "agent-a"


async def test_subscribe_adds_to_channel_subscribers(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    ch = await bus.get_channel("#general")
    assert "agent-a" in ch.subscribers


async def test_subscribe_to_missing_channel_raises(bus: MessageBus) -> None:
    with pytest.raises(ChannelNotFoundError):
        await bus.subscribe("#nonexistent", "agent-a")


async def test_unsubscribe_removes_subscriber(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    await bus.unsubscribe("#general", "agent-a")
    ch = await bus.get_channel("#general")
    assert "agent-a" not in ch.subscribers


async def test_unsubscribe_not_subscribed_raises(bus: MessageBus) -> None:
    with pytest.raises(NotSubscribedError):
        await bus.unsubscribe("#general", "agent-a")


# -- Publish and Receive -----------------------------------------------


async def test_publish_and_receive(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    msg = _make_message(channel="#general", content="hello-nats")
    await bus.publish(msg)
    envelope = await bus.receive("#general", "agent-a", timeout=5.0)
    assert envelope is not None
    assert envelope.message.parts[0].text == "hello-nats"  # type: ignore[union-attr]
    assert envelope.channel_name == "#general"


async def test_publish_to_missing_channel_raises(bus: MessageBus) -> None:
    msg = _make_message(channel="#nonexistent")
    with pytest.raises(ChannelNotFoundError):
        await bus.publish(msg)


async def test_fifo_ordering(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    for i in range(5):
        msg = _make_message(channel="#general", content=f"msg-{i}")
        await bus.publish(msg)
    for i in range(5):
        envelope = await bus.receive("#general", "agent-a", timeout=5.0)
        assert envelope is not None
        assert envelope.message.parts[0].text == f"msg-{i}"  # type: ignore[union-attr]


async def test_fan_out_to_multiple_subscribers(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    await bus.subscribe("#general", "agent-b")
    msg = _make_message(channel="#general", content="broadcast")
    await bus.publish(msg)
    env_a = await bus.receive("#general", "agent-a", timeout=5.0)
    env_b = await bus.receive("#general", "agent-b", timeout=5.0)
    assert env_a is not None
    assert env_b is not None
    assert env_a.message.id == env_b.message.id


async def test_receive_timeout_returns_none(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    envelope = await bus.receive("#general", "agent-a", timeout=0.2)
    assert envelope is None


async def test_receive_not_subscribed_raises(bus: MessageBus) -> None:
    with pytest.raises(NotSubscribedError):
        await bus.receive("#general", "agent-a", timeout=0.1)


_PARK_POLL_INTERVAL_SECONDS: Final[float] = 0.005
"""Inter-poll yield while waiting for a receive coroutine to park.

Promoted from a literal so the magic-number gate has a named target.
The wall-clock 2s deadline above is the load-bearing primitive; this
constant only controls how quickly the test re-checks ``in_flight_fetches``
once the receive task is in flight."""


async def test_receive_on_stopped_bus_raises(bus: MessageBus) -> None:
    await bus.stop()
    with pytest.raises(MessageBusNotRunningError):
        await bus.receive("#general", "agent-a", timeout=0.1)


async def test_receive_returns_none_on_shutdown(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")

    receive_task = asyncio.create_task(bus.receive("#general", "agent-a", timeout=10.0))
    # Wait deterministically until the receive coroutine has parked at
    # the fetch-vs-shutdown race in ``fetch_with_shutdown``. The state
    # populates ``in_flight_fetches`` synchronously before that
    # ``asyncio.wait``, so a non-empty set is the canonical
    # "consumer is parked" signal -- strictly stronger than a fixed
    # timeout heuristic, which only proves the task hasn't *finished*.
    assert isinstance(bus, JetStreamMessageBus)
    # Integration test reaches into bus internals to assert the parked-receive contract.
    state = bus._state
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    try:
        while not state.in_flight_fetches:
            # If the receive task completes before parking it has
            # either raised or returned naturally; either signal must
            # surface immediately rather than waiting out the deadline.
            if receive_task.done():
                # ``await`` re-raises an exception or returns the value
                # so the test fails with the receive coroutine's real
                # error rather than the synthetic "did not park" miss.
                early = await receive_task
                pytest.fail(f"receive() returned before parking (result={early!r})")
            if loop.time() >= deadline:
                pytest.fail("receive() did not park within 2s")
            await asyncio.sleep(_PARK_POLL_INTERVAL_SECONDS)
        assert not receive_task.done(), "receive() returned before bus.stop() fired"
        await bus.stop()
        assert await receive_task is None
    finally:
        # Belt-and-braces cleanup: if any of the asserts above raised,
        # the receive_task may still be pending. Cancel + await so the
        # task does not leak across the test boundary.
        if not receive_task.done():
            receive_task.cancel()
            # ``CancelledError`` is the expected outcome of cancel();
            # swallowing ``Exception`` covers any error the receive
            # coroutine raised after the assert chain failed (e.g. a
            # NATS error that aborted setup before parking) so the
            # cleanup never masks the original test failure with a
            # secondary cancel-time error.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receive_task


# -- Direct Messaging --------------------------------------------------


async def test_direct_creates_channel_lazily(bus: MessageBus) -> None:
    msg = _make_message(sender="agent-a", to="agent-b")
    await bus.send_direct(msg, recipient="agent-b")
    ch = await bus.get_channel("@agent-a:agent-b")
    assert ch.type == ChannelType.DIRECT


async def test_direct_both_receive(bus: MessageBus) -> None:
    msg = _make_message(sender="agent-a", to="agent-b", content="dm-1")
    await bus.send_direct(msg, recipient="agent-b")
    await bus.subscribe("@agent-a:agent-b", "agent-a")
    await bus.subscribe("@agent-a:agent-b", "agent-b")
    # Re-send after both subscribers are registered so each consumer
    # gets its own copy. JetStream durable consumers replay from
    # their creation point, so the pre-subscribe send is not seen.
    msg2 = _make_message(sender="agent-a", to="agent-b", content="dm-2")
    await bus.send_direct(msg2, recipient="agent-b")
    env_a = await bus.receive("@agent-a:agent-b", "agent-a", timeout=5.0)
    env_b = await bus.receive("@agent-a:agent-b", "agent-b", timeout=5.0)
    assert env_a is not None
    assert env_b is not None


async def test_direct_recipient_mismatch_raises(bus: MessageBus) -> None:
    msg = _make_message(sender="agent-a", to="agent-b")
    with pytest.raises(ValueError, match="does not match"):
        await bus.send_direct(msg, recipient="agent-c")


# -- History -----------------------------------------------------------


async def test_history_returns_published_messages(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    for i in range(3):
        msg = _make_message(channel="#general", content=f"msg-{i}")
        await bus.publish(msg)
    history = await bus.get_channel_history("#general")
    assert len(history) >= 3
    # Last 3 entries should be our messages in order.
    last_three = history[-3:]
    assert last_three[0].parts[0].text == "msg-0"  # type: ignore[union-attr]
    assert last_three[1].parts[0].text == "msg-1"  # type: ignore[union-attr]
    assert last_three[2].parts[0].text == "msg-2"  # type: ignore[union-attr]


async def test_history_with_limit(bus: MessageBus) -> None:
    await bus.subscribe("#general", "agent-a")
    for i in range(5):
        msg = _make_message(channel="#general", content=f"msg-{i}")
        await bus.publish(msg)
    history = await bus.get_channel_history("#general", limit=2)
    assert len(history) == 2


async def test_history_limit_zero_returns_empty(bus: MessageBus) -> None:
    await bus.publish(_make_message(channel="#general", content="x"))
    history = await bus.get_channel_history("#general", limit=0)
    assert history == ()


async def test_history_missing_channel_raises(bus: MessageBus) -> None:
    with pytest.raises(ChannelNotFoundError):
        await bus.get_channel_history("#nonexistent")


# -- Factory -----------------------------------------------------------


def test_build_message_bus_selects_nats(nats_url: str) -> None:
    """The factory returns JetStreamMessageBus for backend=nats."""
    config = _make_config(url=nats_url)
    instance = build_message_bus(config)
    assert isinstance(instance, JetStreamMessageBus)


# -- Per-Message TTL (NATS 2.11+) -------------------------------------


async def test_publish_with_ttl_succeeds(bus: MessageBus) -> None:
    """Publishing with ``ttl_seconds`` is accepted and immediate delivery works."""
    await bus.subscribe("#general", "ttl-sub")
    msg = _make_message(channel="#general", content="ephemeral")
    await bus.publish(msg, ttl_seconds=1.0)

    # Immediately receive -- should succeed
    envelope = await bus.receive("#general", "ttl-sub", timeout=5.0)
    assert envelope is not None
    assert envelope.message.parts[0].text == "ephemeral"  # type: ignore[union-attr]


async def test_publish_without_ttl_persists(bus: MessageBus) -> None:
    """A message without TTL should persist according to stream retention."""
    await bus.subscribe("#general", "no-ttl-sub")
    msg = _make_message(channel="#general", content="persistent")
    await bus.publish(msg)

    envelope = await bus.receive("#general", "no-ttl-sub", timeout=5.0)
    assert envelope is not None
    assert envelope.message.parts[0].text == "persistent"  # type: ignore[union-attr]


# -- Batch Publish (pipeline) -----------------------------------------


async def test_batch_publish_all_messages_arrive(bus: MessageBus) -> None:
    """All messages in a batch should be received in order."""
    await bus.subscribe("#general", "batch-sub")
    messages = [
        _make_message(channel="#general", content=f"batch-{i}") for i in range(5)
    ]
    await bus.publish_batch(messages)

    for i in range(5):
        envelope = await bus.receive("#general", "batch-sub", timeout=5.0)
        assert envelope is not None
        assert envelope.message.parts[0].text == f"batch-{i}"  # type: ignore[union-attr]


async def test_batch_publish_empty_is_noop(bus: MessageBus) -> None:
    """An empty batch should succeed without error."""
    await bus.publish_batch([])


async def test_batch_publish_with_ttl(bus: MessageBus) -> None:
    """Batch publish with TTL should work for all messages."""
    await bus.subscribe("#general", "batch-ttl-sub")
    messages = [
        _make_message(channel="#general", content=f"batch-ttl-{i}") for i in range(3)
    ]
    await bus.publish_batch(messages, ttl_seconds=30.0)

    for i in range(3):
        envelope = await bus.receive("#general", "batch-ttl-sub", timeout=5.0)
        assert envelope is not None
        assert envelope.message.parts[0].text == f"batch-ttl-{i}"  # type: ignore[union-attr]
