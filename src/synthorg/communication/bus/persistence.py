"""Shared history and replay accessors for bus backends.

Each bus backend stores per-channel message history differently:
in-memory uses a bounded ``deque``, NATS JetStream uses a stream with
``MaxMsgsPerSubject``. This module exposes a thin ``HistoryAccessor``
protocol plus two implementations so each backend's
``get_channel_history`` is a one-liner that delegates to its accessor.

The split exists so the replay/history query path is unit-testable
independently of the driver (the deque accessor has no NATS
dependency; the JetStream accessor can be smoke-tested without a
full bus instance).
"""

from collections import deque
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from synthorg.communication.errors import ChannelNotFoundError
from synthorg.communication.message import Message
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    BUS_HISTORY_CHANNEL_NOT_FOUND,
)

logger = get_logger(__name__)


@runtime_checkable
class HistoryAccessor(Protocol):
    """Protocol for retrieving bounded per-channel message history.

    Implementations must return messages in chronological order
    (oldest first) and respect the ``limit`` semantics of
    ``MessageBus.get_channel_history``: ``None`` returns all stored
    messages, ``<= 0`` returns an empty tuple, and positive values
    return at most the last ``limit`` messages.

    Implementations must raise :class:`ChannelNotFoundError` when the
    channel does not exist so backends that delegate directly to the
    accessor still satisfy the ``MessageBus.get_channel_history``
    contract (empty history and missing channel are distinguishable).
    """

    async def get_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Return the channel's most recent messages in chronological order."""
        ...


class DequeHistoryAccessor:
    """In-memory ``HistoryAccessor`` backed by per-channel deques.

    Wraps the ``dict[str, deque[Message]]`` structure already used by
    ``InMemoryMessageBus``. Read-only: the bus owns the write path
    and passes the mapping by reference.
    """

    def __init__(
        self,
        histories: Mapping[str, deque[Message]],
    ) -> None:
        self._histories = histories

    async def get_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Return slice of the channel's deque in chronological order.

        Raises:
            ChannelNotFoundError: If *channel_name* has no bucket in
                the backing histories mapping. Returning an empty
                tuple would conflate a missing channel with an empty
                history, which violates the ``MessageBus`` protocol.
        """
        bucket = self._histories.get(channel_name)
        if bucket is None:
            msg = f"Channel not found: {channel_name}"
            logger.warning(BUS_HISTORY_CHANNEL_NOT_FOUND, channel=channel_name)
            raise ChannelNotFoundError(msg, context={"channel": channel_name})
        messages = list(bucket)
        return _apply_limit(messages, limit)


def _apply_limit(
    messages: list[Message],
    limit: int | None,
) -> tuple[Message, ...]:
    """Apply ``MessageBus.get_channel_history`` limit semantics.

    ``None`` returns all messages, ``<= 0`` returns empty, positive
    values return at most the last ``limit`` messages.

    Returns:
        The messages after applying the limit semantics.
    """
    if limit is None:
        return tuple(messages)
    if limit <= 0:
        return ()
    if limit >= len(messages):
        return tuple(messages)
    return tuple(messages[-limit:])
