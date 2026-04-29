"""MessageService -- read + publish facade over the message bus.

Wraps :class:`MessageBus` for channel / history reads, the publish
path, and now operator-driven deletion. ``MessageRepository.delete``
is implemented on both SQLite and Postgres backends; the service
forwards the call and emits an audit-grade
:data:`COMMUNICATION_MESSAGE_DELETED` event on success.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_MESSAGE_DELETED,
    COMMUNICATION_MESSAGE_SENT_VIA_MCP,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.channel import Channel
    from synthorg.communication.message import Message
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


class MessageService:
    """Facade over the message bus for MCP.

    Args:
        bus: Message bus used for channel listings and publishing.
        persistence: Persistence backend whose ``messages`` repository
            owns the durable channel history.
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        persistence: PersistenceBackend,
    ) -> None:
        self._bus = bus
        self._persistence = persistence

    async def list_channels(self) -> Sequence[Channel]:
        """Return all channels the bus is aware of."""
        return tuple(await self._bus.list_channels())

    async def list_messages(
        self,
        *,
        channel: NotBlankStr | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[Sequence[Message], int]:
        """Return message history for a channel, paginated.

        Returns ``(items, total)`` where ``items`` is the requested page
        slice and ``total`` is the unfiltered count for the channel.
        The handler uses ``total`` to build the pagination envelope so
        callers can navigate.  Passing ``channel=None`` returns
        ``((), 0)`` -- an empty page -- without touching persistence.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        if channel is None:
            return ((), 0)
        history = tuple(await self._persistence.messages.get_history(channel))
        total = len(history)
        end = total if limit is None else offset + limit
        return (history[offset:end], total)

    async def get_message(
        self,
        *,
        channel: NotBlankStr,
        message_id: str,
    ) -> Message | None:
        """Return one message by ``(channel, id)`` or ``None``."""
        history = await self._persistence.messages.get_history(channel)
        for msg in history:
            if str(msg.id) == message_id:
                return msg
        return None

    async def send_message(
        self,
        *,
        message: Message,
        actor_id: NotBlankStr,
    ) -> None:
        """Publish ``message`` onto the bus and audit the send.

        ``actor_id`` is the trusted, handler-supplied identity of the
        MCP caller; it drives the audit event so a malicious payload
        cannot spoof ``sender`` in the log.  The payload-side
        ``message.sender`` is still logged as ``sender`` for
        observability but is never treated as the authenticated actor.
        """
        await self._bus.publish(message)
        logger.info(
            COMMUNICATION_MESSAGE_SENT_VIA_MCP,
            channel=message.channel,
            actor_id=actor_id,
            sender=message.sender,
        )

    async def delete_message(
        self,
        *,
        message_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Delete a single message by id.

        ``messages.id`` is globally unique so deletion is scoped by
        id alone. The ``actor_id`` and ``reason`` arguments drive the
        audit log so operator-initiated removals are traceable
        end-to-end. ``channel`` is intentionally absent from this
        contract: callers cannot scope the delete to a channel
        without first reading the message, and accepting an
        unvalidated ``channel`` field would let stale or wrong values
        pollute the audit trail.

        Returns ``True`` if a row was removed, ``False`` if the
        ``message_id`` did not exist.
        """
        deleted = await self._persistence.messages.delete(message_id)
        if deleted:
            logger.info(
                COMMUNICATION_MESSAGE_DELETED,
                message_id=message_id,
                actor_id=actor_id,
                reason=reason,
            )
        return deleted


__all__ = [
    "MessageService",
]
