"""Message repository protocol."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.message import Message
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


class MessageFilterSpec(BaseModel):
    """Filter spec for ``MessageRepository.query``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    channel: NotBlankStr | None = Field(
        default=None,
        description="Filter to messages on this channel",
    )


@runtime_checkable
class MessageRepository(
    AppendOnlyRepository[Message, MessageFilterSpec],
    Protocol,
):
    """Write + history query interface for Message persistence.

    Composes :class:`AppendOnlyRepository`.

    * ``get_history`` returns newest-first within one channel with the
      project's canonical limit default; it is the dashboard hot path
      and the existing controller calls already pass a channel name
      positionally.
    * ``delete`` supports per-message moderation / redaction; the
      generic ``purge_before(threshold)`` is the retention sweeper.
    """

    async def append(self, message: Message) -> None:
        """Persist a message (append-only).

        Args:
            message: The message to persist.

        Raises:
            DuplicateRecordError: If a message with the same ID exists.
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: MessageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Message, ...]:
        """Return messages matching the filter spec, newest first.

        Args:
            filter_spec: Carries optional ``channel`` filter.
            limit: Maximum number of messages to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Messages ordered by timestamp descending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def purge_before(self, threshold: datetime) -> int:
        """Delete messages with ``timestamp < threshold``.

        Returns:
            Number of rows removed.
        """
        ...

    async def get_history(
        self,
        channel: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[Message, ...]:
        """Retrieve message history for a channel (dashboard hot path).

        Args:
            channel: Channel name to query.
            limit: Maximum number of messages to return (newest first).

        Returns:
            Messages ordered by timestamp descending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_id(
        self,
        channel: NotBlankStr,
        message_id: NotBlankStr,
    ) -> Message | None:
        """Fetch a single message by ``(channel, id)``.

        ``messages.id`` is the primary key (globally unique), so the
        lookup is an indexed point read; ``channel`` is an additional
        scoping predicate so a caller cannot read a message off a
        channel it did not address. Replaces the prior
        ``get_history`` full-channel scan in
        :meth:`MessageService.get_message`.

        Args:
            channel: Channel the message must belong to.
            message_id: The unique message identifier.

        Returns:
            The matching :class:`Message`, or ``None`` when no message
            with that id exists on that channel.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, message_id: NotBlankStr) -> bool:
        """Delete a message by id (moderation / redaction).

        Args:
            message_id: The unique message identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if the id did not
            exist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
