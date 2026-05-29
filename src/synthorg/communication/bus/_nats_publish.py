"""Publishing: publish to channels and send direct messages.

Includes message serialization/deserialization, the JetStream
publish-with-ack wrapper, per-message TTL support (NATS 2.11+),
and pipeline batch publishing via async publishes.
"""

import asyncio
from collections.abc import Sequence

from synthorg.communication.bus._nats_channels import (
    direct_subject as _direct_subject,
)
from synthorg.communication.bus._nats_channels import (
    prepare_direct_channel,
    resolve_channel_or_raise,
    subject_for_channel,
)
from synthorg.communication.bus._nats_kv import write_channel_to_kv
from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus._nats_utils import (
    DM_SEPARATOR,
    MAX_BUS_PAYLOAD_BYTES,
    require_running,
)
from synthorg.communication.errors import MessageBusNotRunningError
from synthorg.communication.message import Message
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_BATCH_PUBLISHED,
    COMM_BUS_MESSAGE_TOO_LARGE,
    COMM_BUS_NOT_RUNNING,
    COMM_DIRECT_SENT,
    COMM_MESSAGE_PUBLISHED,
    COMM_SEND_DIRECT_INVALID,
)

logger = get_logger(__name__)


def serialize_message(message: Message) -> bytes:
    """Serialize a Message to JSON bytes for the wire.

    Returns:
        The message as alias-keyed JSON, UTF-8 encoded.
    """
    return message.model_dump_json(by_alias=True).encode("utf-8")


def deserialize_message(data: bytes) -> Message:
    """Reconstruct a Message from wire JSON bytes.

    Returns:
        The deserialised ``Message``.
    """
    return Message.model_validate_json(data.decode("utf-8"))


async def publish_with_ack(
    state: _NatsState,
    subject: str,
    payload: bytes,
    msg_ttl: float | None = None,
) -> None:
    """Publish to JetStream waiting for server ack.

    Args:
        state: NATS connection state.
        subject: JetStream subject to publish to.
        payload: Serialized message bytes.
        msg_ttl: Per-message TTL in seconds (NATS 2.11+).

    Raises:
        MessageBusNotRunningError: If JetStream context is not
            initialized.
    """
    if state.js is None:
        msg = "JetStream context not initialized"
        logger.warning(COMM_BUS_NOT_RUNNING, error=msg, operation="publish_with_ack")
        raise MessageBusNotRunningError(msg)
    await asyncio.wait_for(
        state.js.publish(subject, payload, msg_ttl=msg_ttl),
        timeout=state.nats_config.publish_ack_wait_seconds,
    )


async def publish(
    state: _NatsState,
    message: Message,
    *,
    ttl_seconds: float | None = None,
) -> None:
    """Publish a message to its channel via the JetStream stream.

    Args:
        state: NATS connection state.
        message: The message to publish.
        ttl_seconds: Optional per-message TTL in seconds.

    Raises:
        MessageBusNotRunningError: If the bus is not running.
        ChannelNotFoundError: If the target channel does not exist.
        ValueError: If the serialized message exceeds the payload
            limit.
    """
    async with state.lock:
        require_running(state)
    channel_name = message.channel
    channel = await resolve_channel_or_raise(state, channel_name)
    prefix = state.nats_config.stream_name_prefix
    subject = subject_for_channel(prefix, channel)

    payload = serialize_message(message)
    if len(payload) > MAX_BUS_PAYLOAD_BYTES:
        msg = (
            f"Serialized message exceeds bus payload limit: "
            f"{len(payload)} > {MAX_BUS_PAYLOAD_BYTES}"
        )
        logger.warning(COMM_BUS_MESSAGE_TOO_LARGE, error=msg, channel=channel_name)
        raise ValueError(msg)
    await publish_with_ack(state, subject, payload, msg_ttl=ttl_seconds)

    logger.info(
        COMM_MESSAGE_PUBLISHED,
        channel=channel_name,
        message_id=str(message.id),
        type=str(message.type),
        backend="nats",
        ttl_seconds=ttl_seconds,
    )


async def send_direct(
    state: _NatsState,
    message: Message,
    *,
    recipient: str,
    ttl_seconds: float | None = None,
) -> None:
    """Send a direct message, creating the DIRECT channel lazily.

    Args:
        state: NATS connection state.
        message: The message to send.
        recipient: The recipient agent ID.
        ttl_seconds: Optional per-message TTL in seconds.

    Raises:
        MessageBusNotRunningError: If the bus is not running.
        ValueError: If the recipient does not match ``message.to``,
            an agent ID contains the DM separator, or the serialized
            message exceeds the payload limit.
    """
    sender = message.sender
    if message.to != recipient:
        msg = f"recipient={recipient!r} does not match message.to={message.to!r}"
        logger.warning(COMM_SEND_DIRECT_INVALID, error=msg)
        raise ValueError(msg)
    for agent_id in (sender, recipient):
        if DM_SEPARATOR in agent_id:
            msg = (
                f"Agent ID {agent_id!r} contains the reserved "
                f"separator character {DM_SEPARATOR!r}"
            )
            logger.warning(COMM_SEND_DIRECT_INVALID, error=msg)
            raise ValueError(msg)
    a, b = sorted([sender, recipient])
    pair = (a, b)
    channel_name = f"@{pair[0]}:{pair[1]}"

    async with state.lock:
        require_running(state)
        kv_channel = prepare_direct_channel(state, channel_name, pair)
        state.known_agents.add(sender)
        state.known_agents.add(recipient)

    if kv_channel is not None:
        await write_channel_to_kv(state, kv_channel)

    prefix = state.nats_config.stream_name_prefix
    subject = _direct_subject(prefix, channel_name)
    payload = serialize_message(message)
    if len(payload) > MAX_BUS_PAYLOAD_BYTES:
        msg = (
            f"Serialized direct message exceeds bus payload limit: "
            f"{len(payload)} > {MAX_BUS_PAYLOAD_BYTES}"
        )
        logger.warning(COMM_SEND_DIRECT_INVALID, error=msg, channel=channel_name)
        raise ValueError(msg)
    await publish_with_ack(state, subject, payload, msg_ttl=ttl_seconds)

    logger.info(
        COMM_DIRECT_SENT,
        channel=channel_name,
        sender=sender,
        recipient=recipient,
        message_id=str(message.id),
        backend="nats",
        ttl_seconds=ttl_seconds,
    )


async def publish_batch(
    state: _NatsState,
    messages: Sequence[Message],
    *,
    ttl_seconds: float | None = None,
) -> None:
    """Publish multiple messages using pipelined async publishes.

    Validates all payloads first (fail-fast), then fires pipelined
    ``publish_async`` calls and waits for all acks via
    ``publish_async_completed``.

    If cancelled mid-pipeline, some messages may already have been
    acknowledged by the server.  Callers that need exactly-once
    semantics should use idempotent message IDs.

    Args:
        state: NATS connection state.
        messages: Messages to publish.  Empty is a no-op.
        ttl_seconds: Optional per-message TTL applied to all messages.

    Raises:
        MessageBusNotRunningError: If the bus is not running.
        ChannelNotFoundError: If any target channel does not exist.
        ValueError: If any serialized message exceeds the payload
            limit.
        ExceptionGroup: If one or more publishes fail.
    """
    if not messages:
        return

    # Capture JetStream context under lock to avoid race with stop().
    async with state.lock:
        require_running(state)
        js = state.js
        if js is None:
            msg = "JetStream context not initialized"
            logger.warning(
                COMM_BUS_NOT_RUNNING,
                error=msg,
                operation="publish_batch",
            )
            raise MessageBusNotRunningError(msg)

    # Resolve channels and validate payloads (fail-fast).
    # Cache resolved subjects so repeated channels skip the KV lookup.
    subjects: list[str] = []
    payloads: list[bytes] = []
    prefix = state.nats_config.stream_name_prefix
    subject_cache: dict[str, str] = {}
    for message in messages:
        ch_name = message.channel
        if ch_name not in subject_cache:
            channel = await resolve_channel_or_raise(state, ch_name)
            subject_cache[ch_name] = subject_for_channel(
                prefix,
                channel,
            )
        payload = serialize_message(message)
        if len(payload) > MAX_BUS_PAYLOAD_BYTES:
            msg = (
                f"Serialized message exceeds bus payload limit: "
                f"{len(payload)} > {MAX_BUS_PAYLOAD_BYTES}"
            )
            logger.warning(
                COMM_BUS_MESSAGE_TOO_LARGE,
                error=msg,
                channel=ch_name,
            )
            raise ValueError(msg)
        subjects.append(subject_cache[ch_name])
        payloads.append(payload)

    # Fire pipelined async publishes.
    futures = [
        await js.publish_async(subject, payload, msg_ttl=ttl_seconds)
        for subject, payload in zip(subjects, payloads, strict=True)
    ]

    # Wait for all acks and surface errors.
    await asyncio.wait_for(
        js.publish_async_completed(),
        timeout=state.nats_config.publish_ack_wait_seconds,
    )
    errors: list[Exception] = []
    for future in futures:
        try:
            future.result()
        except Exception as exc:
            reraise_critical(exc)
            errors.append(exc)
    if errors:
        msg = "publish_batch: one or more publishes failed"
        raise ExceptionGroup(msg, errors)

    logger.info(
        COMM_BATCH_PUBLISHED,
        count=len(messages),
        backend="nats",
        ttl_seconds=ttl_seconds,
    )
