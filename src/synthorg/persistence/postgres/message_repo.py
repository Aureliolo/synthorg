# module-kind: repository
"""Postgres repository implementation for Message.

``content`` is stored as TEXT containing a JSON-serialized ``parts``
array (same as SQLite, for protocol compatibility).  ``metadata``
and ``attachments`` use native JSONB.
"""

import json
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.communication.message import Message
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MESSAGE_DELETE_FAILED,
    PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
    PERSISTENCE_MESSAGE_DUPLICATE,
    PERSISTENCE_MESSAGE_FETCH_FAILED,
    PERSISTENCE_MESSAGE_FETCHED,
    PERSISTENCE_MESSAGE_HISTORY_FAILED,
    PERSISTENCE_MESSAGE_HISTORY_FETCHED,
    PERSISTENCE_MESSAGE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    normalize_utc,
    validate_pagination_args,
)

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.message_protocol import MessageFilterSpec

logger = get_logger(__name__)


class PostgresMessageRepository:
    """Postgres implementation of the MessageRepository protocol.

    ``content`` is stored as TEXT containing a JSON-serialized ``parts``
    array (same as SQLite, for protocol compatibility).  ``metadata``
    and ``attachments`` use native JSONB.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, message: Message) -> None:
        """Persist a message (append-only per AppendOnlyRepository).

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        data = message.model_dump(mode="json")
        msg_id = str(message.id)

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO messages (
                        id, timestamp, sender, "to", type, priority,
                        channel, content, attachments, metadata
                    ) VALUES (
                        %(id)s, %(timestamp)s, %(sender)s, %(to)s, %(type)s,
                        %(priority)s, %(channel)s, %(content)s, %(attachments)s,
                        %(metadata)s
                    )
                    """,
                    {
                        "id": msg_id,
                        "timestamp": message.timestamp,
                        "sender": data["sender"],
                        "to": data["to"],
                        "type": data["type"],
                        "priority": data["priority"],
                        "channel": data["channel"],
                        "content": json.dumps(data["parts"]),
                        "attachments": Jsonb(data.get("attachments", [])),
                        "metadata": Jsonb(data["metadata"]),
                    },
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            err_msg = f"Message {msg_id} already exists"
            logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, message_id=msg_id)
            raise DuplicateRecordError(err_msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save message {msg_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _row_to_message(self, row: dict[str, Any]) -> Message:
        """Reconstruct a Message from a Postgres dict_row.

        Returns:
            Result of type ``Message``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = dict(row)
            # Map DB column "sender" to Message's "from" alias.
            data["from"] = data.pop("sender")
            # Parts are stored as JSON in the content column.
            content = data.pop("content")
            data["parts"] = json.loads(content) if isinstance(content, str) else content
            # attachments round-trips through JSONB as a Python list;
            # leave it in place for Pydantic to validate.
            # metadata comes back as a Python dict (JSONB).
            return Message.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            msg_id = row.get("id", "unknown")
            msg = f"Failed to deserialize message {msg_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
                message_id=msg_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get_history(
        self,
        channel: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[Message, ...]:
        """Retrieve message history for a channel, newest first.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
        ):
            msg = f"limit must be a positive integer, got {limit!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=channel,
                error=msg,
            )
            raise QueryError(msg)
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages "
            "WHERE channel = %s "
            "ORDER BY timestamp DESC"
        )
        params: list[object] = [channel]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to fetch message history for channel {channel!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=channel,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        messages = tuple(self._row_to_message(row) for row in rows)
        logger.debug(
            PERSISTENCE_MESSAGE_HISTORY_FETCHED,
            channel=channel,
            count=len(messages),
        )
        return messages

    async def get_by_id(
        self,
        channel: str,
        message_id: str,
    ) -> Message | None:
        """Fetch one message by ``(channel, id)`` via the PK point read.

        The ``id`` predicate alone resolves the row (it is the primary
        key); the extra ``channel`` predicate is a deliberate scoping
        guard so a caller holding only a message id cannot read a
        message outside the channel it asked for.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages "
            "WHERE id = %s AND channel = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, [message_id, channel])
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch message {message_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_FETCH_FAILED,
                channel=channel,
                message_id=message_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        message = self._row_to_message(row)
        logger.debug(
            PERSISTENCE_MESSAGE_FETCHED,
            channel=channel,
            message_id=message_id,
        )
        return message

    async def query(
        self,
        filter_spec: MessageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Message, ...]:
        """Return messages matching the filter spec, newest first.

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_MESSAGE_HISTORY_FAILED
        )
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages"
        )
        params: list[object] = []
        if filter_spec.channel is not None:
            sql += " WHERE channel = %s"
            params.append(filter_spec.channel)
        sql += " ORDER BY timestamp DESC, id ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query messages"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=filter_spec.channel,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_message(row) for row in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete messages with ``timestamp < threshold`` (retention).

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM messages WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge messages by threshold"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount

    async def delete(self, message_id: NotBlankStr) -> bool:
        """Delete a single message by id (bespoke per ADR D7, moderation).

        Returns ``True`` when a row was removed, ``False`` when the id
        did not exist. The audit-grade mutation log is emitted by
        :class:`MessageService.delete_message`; the repository never
        logs mutations itself (persistence-boundary rule, see
        ``docs/reference/persistence-boundary.md``).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM messages WHERE id = %s",
                    (message_id,),
                )
                await conn.commit()
                deleted = cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete message {message_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                message_id=message_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
