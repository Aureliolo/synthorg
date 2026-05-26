"""Durable pull consumer lifecycle: subscribe and unsubscribe.

Each ``(channel_name, subscriber_id)`` pair maps to a durable pull
consumer. This module handles creation and teardown of those consumers.
"""

from datetime import UTC, datetime
from typing import Any

from synthorg.communication.bus._nats_channels import (
    durable_name,
    resolve_channel_or_raise,
    subject_for_channel,
)
from synthorg.communication.bus._nats_kv import write_channel_to_kv
from synthorg.communication.bus._nats_state import _NatsState  # noqa: TC001
from synthorg.communication.bus._nats_utils import (
    CONSUMER_ACK_WAIT_MULTIPLIER,
    raise_not_subscribed,
    require_running,
)
from synthorg.communication.bus.errors import BusStreamError
from synthorg.communication.channel import Channel  # noqa: TC001
from synthorg.communication.subscription import Subscription
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_DUPLICATE_SUBSCRIPTION_DISCARDED,
    COMM_SUBSCRIPTION_CREATED,
    COMM_SUBSCRIPTION_REMOVED,
)

logger = get_logger(__name__)


async def create_pull_consumer(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
    channel: Channel,
) -> Any:
    """Create a durable pull consumer for (channel, subscriber).

    Returns the subscription object. Does NOT acquire ``state.lock``
    so callers can perform the network I/O outside the lock.
    """
    from nats.js.api import ConsumerConfig  # noqa: PLC0415

    if state.js is None:
        msg = "JetStream context not initialized"
        raise BusStreamError(msg)
    prefix = state.nats_config.stream_name_prefix
    subject = subject_for_channel(prefix, channel)
    durable = durable_name(channel_name, subscriber_id)
    # ``max_ack_pending`` gives the NATS consumer the same
    # per-subscriber bound that the in-memory bus applies to its
    # ``asyncio.Queue(maxsize=...)``. JetStream pauses delivery to a
    # consumer whose unacked count reaches this cap, preventing
    # broker-side accumulation for a slow agent.
    consumer_config = ConsumerConfig(
        durable_name=durable,
        ack_wait=(
            state.nats_config.publish_ack_wait_seconds * CONSUMER_ACK_WAIT_MULTIPLIER
        ),
        max_deliver=1,
        max_ack_pending=state.config.retention.max_subscriber_queue_size,
        filter_subject=subject,
    )
    return await state.js.pull_subscribe(
        subject=subject,
        durable=durable,
        stream=state.stream_name,
        config=consumer_config,
    )


async def subscribe(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
) -> Subscription:
    """Subscribe an agent to a channel via a durable pull consumer."""
    async with state.lock:
        require_running(state)
    await resolve_channel_or_raise(state, channel_name)

    # Snapshot state under lock, then release for network I/O.
    async with state.lock:
        require_running(state)
        channel = state.channels[channel_name]
        state.known_agents.add(subscriber_id)
        key = (channel_name, subscriber_id)
        needs_consumer = key not in state.subscriptions

    # Network I/O outside the lock so other bus operations are not blocked.
    sub = None
    if needs_consumer:
        sub = await create_pull_consumer(state, channel_name, subscriber_id, channel)

    # Store results under lock; KV write deferred to outside the lock.
    updated_channel = None
    cleanup_sub = None
    async with state.lock:
        if sub is not None:
            if key not in state.subscriptions:
                state.subscriptions[key] = sub
            else:
                # Another coroutine won the race -- discard our sub.
                cleanup_sub = sub

        channel = state.channels[channel_name]
        if subscriber_id not in channel.subscribers:
            new_subs = (*channel.subscribers, subscriber_id)
            updated_channel = channel.model_copy(
                update={"subscribers": new_subs},
            )
            state.channels[channel_name] = updated_channel

    if cleanup_sub is not None:
        # Do NOT call ``cleanup_sub.unsubscribe()`` here. JetStream
        # durable consumers are keyed by ``durable_name(channel,
        # subscriber)``; two concurrent ``pull_subscribe`` calls with
        # the same durable name return distinct client-side
        # ``Subscription`` objects that reference the **same**
        # server-side consumer. Calling ``unsubscribe()`` on this
        # duplicate would delete that shared consumer and break the
        # winning coroutine's subscription on the next fetch. Dropping
        # the local reference is the correct cleanup: the server-side
        # consumer remains bound to the winner in ``state.subscriptions``.
        # Use the dedicated discard event (not ``COMM_SUBSCRIPTION_REMOVED``)
        # so unsubscribe metrics / alerts / audit trails are not inflated
        # by race-loss discards.
        logger.debug(
            COMM_DUPLICATE_SUBSCRIPTION_DISCARDED,
            channel=channel_name,
            subscriber=subscriber_id,
        )

    if updated_channel is not None:
        await write_channel_to_kv(state, updated_channel)

    logger.info(
        COMM_SUBSCRIPTION_CREATED,
        channel=channel_name,
        subscriber=subscriber_id,
        backend="nats",
    )
    return Subscription(
        channel_name=channel_name,
        subscriber_id=subscriber_id,
        subscribed_at=datetime.now(UTC),
    )


async def unsubscribe(
    state: _NatsState,
    channel_name: str,
    subscriber_id: str,
) -> None:
    """Remove a subscription and tear down the pull consumer."""
    async with state.lock:
        require_running(state)
        if channel_name not in state.channels:
            raise_not_subscribed(channel_name, subscriber_id)
        channel = state.channels[channel_name]
        if subscriber_id not in channel.subscribers:
            raise_not_subscribed(channel_name, subscriber_id)
        new_subs = tuple(s for s in channel.subscribers if s != subscriber_id)
        updated = channel.model_copy(
            update={"subscribers": new_subs},
        )
        state.channels[channel_name] = updated
        key = (channel_name, subscriber_id)
        sub: Any = state.subscriptions.pop(key, None)
        # Clear the overflow-log rate-limit entry alongside the
        # subscription so repeatedly subscribing and unsubscribing the
        # same ``(channel, subscriber)`` key cannot leak stale entries
        # into ``state.last_overflow_log``. The dict is otherwise only
        # pruned on the receive path (probe failure / healthy
        # consumer), which never runs for an already-unsubscribed
        # pair.
        state.last_overflow_log.pop(key, None)

    await write_channel_to_kv(state, updated)

    if sub is not None:
        try:
            await sub.unsubscribe()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMM_SUBSCRIPTION_REMOVED,
                channel=channel_name,
                subscriber=subscriber_id,
                backend="nats",
                phase="unsubscribe_consumer_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    logger.info(
        COMM_SUBSCRIPTION_REMOVED,
        channel=channel_name,
        subscriber=subscriber_id,
        backend="nats",
    )
