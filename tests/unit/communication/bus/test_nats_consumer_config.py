"""NATS subscriber bounding parity tests.

The in-memory bus bounds each subscriber's in-flight queue via
``MessageRetentionConfig.max_subscriber_queue_size``. These tests
assert NATS reaches parity by wiring the same value through to
``ConsumerConfig.max_ack_pending`` on the durable pull consumer:
JetStream pauses delivery to a consumer whose unacked count hits this
cap, preventing unbounded broker-side accumulation per subscriber.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.bus._nats_consumers import create_pull_consumer
from synthorg.communication.channel import Channel
from synthorg.communication.config import (
    MessageBusConfig,
    MessageRetentionConfig,
    NatsConfig,
)
from synthorg.communication.enums import ChannelType, MessageBusBackend


def _state_stub(max_subscriber_queue_size: int) -> MagicMock:
    """Build a minimal ``_NatsState`` stub exposing the attributes
    ``create_pull_consumer`` reads: ``js``, ``nats_config``,
    ``stream_name``, ``config.retention``.
    """
    js = MagicMock()
    # Capture the ConsumerConfig the consumer factory builds.
    js.pull_subscribe = AsyncMock(return_value=MagicMock(name="pull-sub"))

    nats_config = NatsConfig()
    config = MessageBusConfig(
        backend=MessageBusBackend.NATS,
        channels=("#engineering",),
        retention=MessageRetentionConfig(
            max_subscriber_queue_size=max_subscriber_queue_size,
        ),
        nats=nats_config,
    )
    state = MagicMock()
    state.js = js
    state.nats_config = nats_config
    state.stream_name = "SYNTHORG_BUS"
    state.config = config
    return state


@pytest.mark.unit
class TestNatsConsumerConfig:
    """``create_pull_consumer`` must wire ``max_ack_pending`` from config."""

    async def test_consumer_config_sets_max_ack_pending_from_retention(self) -> None:
        """The NATS consumer must cap in-flight delivery at the shared
        ``max_subscriber_queue_size`` value to achieve parity with the
        in-memory subscriber queue bound.
        """
        max_queue = 128
        state = _state_stub(max_queue)
        channel = Channel(name="#engineering", type=ChannelType.TOPIC)

        await create_pull_consumer(
            state,  # type: ignore[arg-type]
            channel_name=channel.name,
            subscriber_id="agent-alice",
            channel=channel,
        )

        state.js.pull_subscribe.assert_awaited_once()
        call_kwargs = state.js.pull_subscribe.call_args.kwargs
        consumer_config = call_kwargs["config"]
        assert consumer_config.max_ack_pending == max_queue, (
            f"ConsumerConfig.max_ack_pending must equal "
            f"retention.max_subscriber_queue_size ({max_queue}); "
            f"got {consumer_config.max_ack_pending}"
        )
