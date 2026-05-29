"""Channel management: creation, resolution, listing, and subject helpers.

Handles the mapping between SynthOrg channel names and JetStream
subject tokens, as well as channel creation/resolution backed by the
KV bucket for multi-process discovery.
"""

from synthorg.communication.bus._nats_kv import (
    create_channel_in_kv,
    load_channel_from_kv,
    scan_kv_channels,
)
from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.bus._nats_utils import (
    SUBJECT_CHANNEL_TOKEN,
    SUBJECT_DIRECT_TOKEN,
    encode_token,
    raise_channel_not_found,
    require_running,
)
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType
from synthorg.communication.errors import ChannelAlreadyExistsError
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_CHANNEL_ALREADY_EXISTS,
    COMM_CHANNEL_CREATED,
)

logger = get_logger(__name__)


def channel_subject(prefix: str, channel_name: str) -> str:
    """Compute the stream subject for a TOPIC/BROADCAST channel.

    Returns:
        The encoded NATS subject for the channel.
    """
    pfx = prefix.lower()
    return f"{pfx}.bus.{SUBJECT_CHANNEL_TOKEN}.{encode_token(channel_name)}"


def direct_subject(prefix: str, channel_name: str) -> str:
    """Compute the stream subject for a DIRECT channel.

    Returns:
        The encoded NATS subject for the direct channel.
    """
    pfx = prefix.lower()
    return f"{pfx}.bus.{SUBJECT_DIRECT_TOKEN}.{encode_token(channel_name)}"


def subject_for_channel(prefix: str, ch: Channel) -> str:
    """Pick the correct subject based on channel type.

    Returns:
        The direct subject for DIRECT channels, otherwise the topic
        subject.
    """
    if ch.type == ChannelType.DIRECT:
        return direct_subject(prefix, ch.name)
    return channel_subject(prefix, ch.name)


def durable_name(channel_name: str, subscriber_id: str) -> str:
    """Compute a safe durable consumer name.

    Returns:
        The encoded ``<channel>__<subscriber>`` durable name.
    """
    return f"{encode_token(channel_name)}__{encode_token(subscriber_id)}"


def prepare_direct_channel(
    state: _NatsState,
    channel_name: str,
    pair: tuple[str, str],
) -> Channel | None:
    """Compute the DIRECT channel state change under ``state.lock``.

    Updates ``state.channels`` in-place and returns the channel to
    persist to KV, or ``None`` if no KV write is needed. The caller
    must perform the KV write outside the lock.

    Args:
        state: Shared bus state.
        channel_name: Deterministic direct-channel name (``@a:b``).
        pair: Sorted tuple of the two participant agent IDs.

    Must be called under ``state.lock``.

    Returns:
        The channel to persist to KV, or ``None`` when no KV write is
        needed (the pair is already covered).
    """
    if channel_name in state.channels:
        current = state.channels[channel_name]
        pair_set = set(pair)
        if not pair_set.issubset(set(current.subscribers)):
            new_subs = tuple(sorted(set(current.subscribers) | pair_set))
            updated = current.model_copy(
                update={"subscribers": new_subs},
            )
            state.channels[channel_name] = updated
            return updated
        return None

    ch = Channel(
        name=channel_name,
        type=ChannelType.DIRECT,
        subscribers=pair,
    )
    state.channels[channel_name] = ch
    logger.info(
        COMM_CHANNEL_CREATED,
        channel=channel_name,
        type=str(ChannelType.DIRECT),
        backend="nats",
    )
    return ch


async def create_channel(state: _NatsState, ch: Channel) -> Channel:
    """Create a new channel.

    Uses an atomic KV create to enforce cross-process uniqueness:
    ``kv.create()`` fails with ``KeyWrongLastSequenceError`` if the
    key already exists, which is translated to
    ``ChannelAlreadyExistsError``.

    Returns:
        The created channel.

    Raises:
        MessageBusNotRunningError: If not running.
        ChannelAlreadyExistsError: If the channel already exists
            (locally or in the KV bucket).
    """
    async with state.lock:
        require_running(state)
        if ch.name in state.channels:
            logger.warning(
                COMM_CHANNEL_ALREADY_EXISTS,
                channel=ch.name,
            )
            msg = f"Channel already exists: {ch.name}"
            raise ChannelAlreadyExistsError(
                msg,
                context={"channel": ch.name},
            )

    # Atomic KV create -- raises ChannelAlreadyExistsError if a peer
    # already created this channel, BusStreamError on transport failure.
    await create_channel_in_kv(state, ch)

    async with state.lock:
        state.channels[ch.name] = ch
    logger.info(
        COMM_CHANNEL_CREATED,
        channel=ch.name,
        type=str(ch.type),
        backend="nats",
    )
    return ch


async def resolve_channel_or_raise(
    state: _NatsState,
    channel_name: str,
) -> Channel:
    """Return a Channel from local cache or JetStream KV.

    Shared by multiple public methods so a second process can observe
    channels created by another process on the same stream.
    """
    async with state.lock:
        cached = state.channels.get(channel_name)
    if cached is not None:
        return cached

    loaded = await load_channel_from_kv(state, channel_name)
    if loaded is None:
        raise_channel_not_found(channel_name)

    async with state.lock:
        existing = state.channels.get(channel_name)
        if existing is not None:
            return existing
        state.channels[channel_name] = loaded
        return loaded


async def list_channels(state: _NatsState) -> tuple[Channel, ...]:
    """List all channels, including those created by peer processes.

    Returns:
        All channels, merging local cache with KV-discovered peers.
    """
    kv_channels = await scan_kv_channels(state)
    async with state.lock:
        for ch in kv_channels:
            if ch.name not in state.channels:
                state.channels[ch.name] = ch
        return tuple(state.channels.values())
