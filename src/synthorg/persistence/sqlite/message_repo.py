# module-kind: repository
"""SQLite repository implementation for Message."""

import json
import sqlite3
from datetime import datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.communication.message import Message
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.message import (
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
    format_iso_utc,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence.message_protocol import MessageFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)


class SQLiteMessageRepository:
    """SQLite implementation of the MessageRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _safe_rollback(self, msg_id: str) -> None:
        """Best-effort rollback on the shared aiosqlite connection.

        A secondary rollback failure must not mask the original write
        error, but we DO log it because a tainted shared connection is
        worth a trail in observability. Without this rollback, a failed
        write inside the shared transaction poisons it for every sibling
        repo holding the same ``aiosqlite.Connection``. Mirrors the
        pattern used by the 37 sibling repos in this package.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def append(self, message: Message) -> None:
        """Persist a message (append-only per AppendOnlyRepository).

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
        """
        data = message.model_dump(mode="json")
        msg_id = str(message.id)

        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO messages (
    id, timestamp, sender, "to", type, priority,
    channel, content, attachments, metadata
) VALUES (
    :id, :timestamp, :sender, :to, :type, :priority,
    :channel, :content, :attachments, :metadata
)""",
                    {
                        "id": msg_id,
                        # UTC-normalised ISO so ``purge_before`` /
                        # ``get_history`` ordering compare correctly
                        # regardless of the caller's original offset.
                        "timestamp": format_iso_utc(
                            normalize_utc(message.timestamp),
                        ),
                        "sender": data["sender"],
                        "to": data["to"],
                        "type": data["type"],
                        "priority": data["priority"],
                        "channel": data["channel"],
                        "content": json.dumps(data["parts"]),
                        "attachments": json.dumps(data.get("attachments", [])),
                        "metadata": json.dumps(data["metadata"]),
                    },
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._safe_rollback(msg_id)
                if is_unique_constraint_error(exc):
                    err_msg = f"Message {msg_id} already exists"
                    logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, message_id=msg_id)
                    raise DuplicateRecordError(err_msg) from exc
                # Other integrity errors (NOT NULL, different UNIQUE).
                msg = f"Failed to save message {msg_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_SAVE_FAILED,
                    message_id=msg_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(msg_id)
                msg = f"Failed to save message {msg_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_SAVE_FAILED,
                    message_id=msg_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        """Reconstruct a Message from a database row.

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
            data["parts"] = json.loads(data.pop("content"))
            raw_attachments = data.get("attachments")
            data["attachments"] = json.loads(raw_attachments) if raw_attachments else []
            data["metadata"] = json.loads(data["metadata"])
            return Message.model_validate(data)
        except (
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            TypeError,
        ) as exc:
            msg_id = row["id"] if row else "unknown"
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
        offset: int = 0,
    ) -> tuple[Message, ...]:
        """Retrieve a bounded page of message history, newest first.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if limit is not None and limit < 1:
            msg = f"limit must be a positive integer, got {limit}"
            raise QueryError(msg)
        if offset < 0:
            msg = f"offset must be non-negative, got {offset}"
            raise QueryError(msg)
        sql = """\
SELECT id, timestamp, sender, "to", type, priority,
       channel, content, attachments, metadata
FROM messages
WHERE channel = ?
ORDER BY timestamp DESC"""
        params: list[object] = [channel]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        # SQLite requires a LIMIT clause before OFFSET; the protocol
        # default keeps ``limit`` present so the OFFSET always has one.
        if offset:
            sql += " OFFSET ?"
            params.append(offset)

        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = """\
SELECT id, timestamp, sender, "to", type, priority,
       channel, content, attachments, metadata
FROM messages
WHERE id = ? AND channel = ?"""
        try:
            async with self._db.execute(sql, [message_id, channel]) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_MESSAGE_HISTORY_FAILED
        )
        sql = """\
SELECT id, timestamp, sender, "to", type, priority,
       channel, content, attachments, metadata
FROM messages"""
        params: list[object] = []
        if filter_spec.channel is not None:
            sql += " WHERE channel = ?"
            params.append(filter_spec.channel)
        sql += " ORDER BY timestamp DESC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        ``threshold`` must be timezone-aware: a naive value compared
        against UTC-formatted stored timestamps would silently delete
        the wrong window.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
            raise QueryError(msg)
        aware_threshold = normalize_utc(threshold)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM messages WHERE timestamp < ?",
                    (format_iso_utc(aware_threshold),),
                ) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to purge messages by threshold"
                logger.warning(
                    PERSISTENCE_MESSAGE_DELETE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return _db_rowcount

    async def delete(self, message_id: NotBlankStr) -> bool:
        """Delete a single message by id (bespoke per ADR D7, moderation).

        Returns ``True`` when a row was removed, ``False`` when the id
        did not exist. Concurrent writes are serialized through the
        shared backend write context. The audit-grade mutation log is
        emitted by :class:`MessageService.delete_message`; the
        repository never logs mutations itself (persistence-boundary
        rule, see ``docs/reference/persistence-boundary.md``).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM messages WHERE id = ?",
                    (message_id,),
                ) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete message {message_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_DELETE_FAILED,
                    message_id=message_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return _db_rowcount > 0
