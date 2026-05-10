"""Unit tests for NATS publish module (TTL + batch).

Tests exercise per-message TTL passthrough and pipeline batch
publishing by mocking the JetStream context. No live NATS
connection required.
"""

# mypy: disable-error-code="union-attr,method-assign"

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.communication.bus._nats_publish import (
    publish,
    publish_batch,
    publish_with_ack,
    send_direct,
)
from synthorg.communication.bus._nats_state import _NatsState
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
from synthorg.communication.errors import MessageBusNotRunningError
from synthorg.communication.message import Message, TextPart

pytestmark = pytest.mark.unit


def _make_state(*, running: bool = True) -> _NatsState:
    """Create a minimal _NatsState with mocked NATS primitives."""
    config = MessageBusConfig(
        backend=MessageBusBackend.NATS,
        channels=("#test",),
        retention=MessageRetentionConfig(max_messages_per_channel=100),
        nats=NatsConfig(
            url="nats://localhost:4222",
            stream_name_prefix="TEST",
            publish_ack_wait_seconds=5.0,
        ),
    )
    nats_config = config.nats
    assert nats_config is not None
    state = _NatsState(
        config=config,
        nats_config=nats_config,
        stream_name="TEST_BUS",
        kv_bucket_name="TEST_BUS_CHANNELS",
    )
    state.running = running
    state.js = AsyncMock()
    # Set up publish to return a PubAck-like result
    state.js.publish = AsyncMock(return_value=MagicMock(seq=1))
    # Set up async publish pipeline
    future: asyncio.Future[SimpleNamespace] = asyncio.get_running_loop().create_future()
    future.set_result(SimpleNamespace(seq=1))
    state.js.publish_async = AsyncMock(return_value=future)
    state.js.publish_async_completed = AsyncMock()
    # Pre-register the #test channel
    state.channels["#test"] = Channel(
        name="#test",
        type=ChannelType.TOPIC,
        subscribers=(),
    )
    return state


def _make_message(
    *,
    channel: str = "#test",
    sender: str = "agent-a",
    to: str = "agent-b",
) -> Message:
    """Create a minimal test message."""
    return Message(
        timestamp=datetime.now(UTC),
        sender=sender,
        to=to,
        type=MessageType.TASK_UPDATE,
        channel=channel,
        parts=(TextPart(text="test content"),),
    )


class TestPublishWithAckTTL:
    """Verify publish_with_ack passes msg_ttl to JetStream."""

    async def test_passes_ttl_to_jetstream(self) -> None:
        state = _make_state()
        await publish_with_ack(state, "test.subject", b"payload", msg_ttl=30.0)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") == 30.0

    async def test_none_ttl_passes_none(self) -> None:
        state = _make_state()
        await publish_with_ack(state, "test.subject", b"payload", msg_ttl=None)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") is None

    async def test_raises_when_js_not_initialized(self) -> None:
        state = _make_state()
        state.js = None

        with pytest.raises(MessageBusNotRunningError):
            await publish_with_ack(state, "test.subject", b"payload", msg_ttl=10.0)

    async def test_zero_ttl_passes_through(self) -> None:
        state = _make_state()
        await publish_with_ack(state, "test.subject", b"payload", msg_ttl=0.0)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") == 0.0

    async def test_negative_ttl_passes_through(self) -> None:
        """Negative TTL is passed to NATS; server decides validity."""
        state = _make_state()
        await publish_with_ack(state, "test.subject", b"payload", msg_ttl=-1.0)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") == -1.0


class TestPublishTTL:
    """Verify publish() forwards ttl_seconds through the stack."""

    async def test_forwards_ttl_to_publish_with_ack(self) -> None:
        state = _make_state()
        msg = _make_message()

        await publish(state, msg, ttl_seconds=60.0)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") == 60.0

    async def test_none_ttl_default(self) -> None:
        state = _make_state()
        msg = _make_message()

        await publish(state, msg)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") is None


class TestSendDirectTTL:
    """Verify send_direct() forwards ttl_seconds."""

    async def test_passes_ttl_through(self) -> None:
        state = _make_state()
        msg = _make_message(sender="agent-a", to="agent-b")

        await send_direct(state, msg, recipient="agent-b", ttl_seconds=15.0)

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") == 15.0

    async def test_none_ttl_default(self) -> None:
        state = _make_state()
        msg = _make_message(sender="agent-a", to="agent-b")

        await send_direct(state, msg, recipient="agent-b")

        state.js.publish.assert_awaited_once()
        _, kwargs = state.js.publish.call_args
        assert kwargs.get("msg_ttl") is None


class TestPublishBatch:
    """Verify batch publish uses the async pipeline."""

    async def test_uses_async_pipeline(self) -> None:
        state = _make_state()
        messages = [_make_message() for _ in range(3)]

        await publish_batch(state, messages)

        assert state.js.publish_async.await_count == 3
        state.js.publish_async_completed.assert_awaited_once()

    async def test_empty_batch_is_noop(self) -> None:
        state = _make_state()

        await publish_batch(state, [])

        state.js.publish_async.assert_not_awaited()
        state.js.publish_async_completed.assert_not_awaited()

    async def test_propagates_ttl(self) -> None:
        state = _make_state()
        messages = [_make_message() for _ in range(2)]

        await publish_batch(state, messages, ttl_seconds=45.0)

        for call in state.js.publish_async.call_args_list:
            _, kwargs = call
            assert kwargs.get("msg_ttl") == 45.0

    async def test_validates_all_before_sending(self) -> None:
        state = _make_state()
        good_msg = _make_message()
        # Create an oversized message by patching serialize
        oversized = _make_message()

        with patch(
            "synthorg.communication.bus._nats_publish.serialize_message",
        ) as mock_serialize:
            mock_serialize.side_effect = [
                b"normal",
                b"x" * (4 * 1024 * 1024 + 1),  # exceeds 4 MB
            ]
            with pytest.raises(ValueError, match="exceeds bus payload limit"):
                await publish_batch(state, [good_msg, oversized])

        # No messages should have been published
        state.js.publish_async.assert_not_awaited()

    async def test_surfaces_publish_error(self) -> None:
        state = _make_state()
        messages = [_make_message()]

        # Make the future resolve with an error
        loop = asyncio.get_running_loop()
        error_future: asyncio.Future[MagicMock] = loop.create_future()
        error_future.set_exception(RuntimeError("publish failed"))
        state.js.publish_async = AsyncMock(return_value=error_future)

        with pytest.raises(ExceptionGroup) as exc_info:
            await publish_batch(state, messages)
        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], RuntimeError)
        assert "publish failed" in str(exc_info.value.exceptions[0])

    async def test_raises_when_not_running(self) -> None:
        state = _make_state(running=False)
        messages = [_make_message()]

        with pytest.raises(MessageBusNotRunningError):
            await publish_batch(state, messages)

    async def test_caches_resolved_subjects(self) -> None:
        """Repeated channels should only resolve once (cache hit)."""
        state = _make_state()
        messages = [_make_message(channel="#test") for _ in range(3)]

        await publish_batch(state, messages)

        assert state.js.publish_async.await_count == 3
        state.js.publish_async_completed.assert_awaited_once()

    async def test_multi_channel_batch(self) -> None:
        """Batch with messages on different channels."""
        state = _make_state()
        # Register a second channel
        state.channels["#other"] = Channel(
            name="#other",
            type=ChannelType.TOPIC,
            subscribers=(),
        )
        messages = [
            _make_message(channel="#test"),
            _make_message(channel="#other"),
            _make_message(channel="#test"),
        ]

        await publish_batch(state, messages)

        assert state.js.publish_async.await_count == 3
        state.js.publish_async_completed.assert_awaited_once()

    async def test_multiple_errors_collected(self) -> None:
        """All future errors are surfaced, not just the first."""
        state = _make_state()
        messages = [_make_message(), _make_message()]

        loop = asyncio.get_running_loop()
        futures = []
        for i in range(2):
            f: asyncio.Future[MagicMock] = loop.create_future()
            f.set_exception(RuntimeError(f"error-{i}"))
            futures.append(f)
        state.js.publish_async = AsyncMock(side_effect=futures)

        with pytest.raises(ExceptionGroup) as exc_info:
            await publish_batch(state, messages)
        assert len(exc_info.value.exceptions) == 2

    async def test_timeout_on_completion(self) -> None:
        """publish_async_completed timeout raises TimeoutError."""
        state = _make_state()
        messages = [_make_message()]

        # Make completion never return
        async def hang_forever() -> None:
            await asyncio.Event().wait()

        state.js.publish_async_completed = hang_forever
        state.nats_config = state.nats_config.model_copy(
            update={"publish_ack_wait_seconds": 0.1},
        )

        with pytest.raises(TimeoutError):
            await publish_batch(state, messages)

    async def test_channel_not_found_in_batch(self) -> None:
        """Batch with an unknown channel raises ChannelNotFoundError."""
        from synthorg.communication.errors import ChannelNotFoundError

        state = _make_state()
        msg = _make_message(channel="#unknown")

        with pytest.raises(ChannelNotFoundError):
            await publish_batch(state, [msg])

        # No messages should have been published
        state.js.publish_async.assert_not_awaited()


class TestPublishOversizedPayload:
    """Verify oversized messages are rejected."""

    async def test_oversized_message_raises(self) -> None:
        state = _make_state()
        msg = _make_message()

        with patch(
            "synthorg.communication.bus._nats_publish.serialize_message",
        ) as mock_serialize:
            mock_serialize.return_value = b"x" * (4 * 1024 * 1024 + 1)
            with pytest.raises(ValueError, match="exceeds bus payload limit"):
                await publish(state, msg)
