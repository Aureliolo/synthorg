"""SQLite repository for the conversation header (id-keyed CRUD + CAS)."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.core.enums import ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.models import Conversation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.conversation import (
    PERSISTENCE_CONVERSATION_FAILED,
    PERSISTENCE_CONVERSATION_FETCHED,
    PERSISTENCE_CONVERSATION_LISTED,
)
from synthorg.persistence._conversation_marshalling import row_to_conversation
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.sqlite.conversation_repo._base import (
    _MAX_PAGE_LIMIT,
    _safe_rollback,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"updated_at"})

_CONVERSATIONS_UPSERT_SQL = """
    INSERT INTO conversations (
        id, created_by, created_at, updated_at, status, kind
    ) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        created_by = excluded.created_by,
        created_at = excluded.created_at,
        updated_at = excluded.updated_at,
        status = excluded.status,
        kind = excluded.kind
"""

_CONVERSATION_COLUMNS = "id, created_by, created_at, updated_at, status, kind"


class SQLiteConversationRepository:
    """SQLite-backed conversation header repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager serialising writes on the
            shared connection (``SQLitePersistenceBackend.write_context``
            in production).
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: Conversation) -> None:
        """Upsert a conversation header.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            entity.id,
            entity.created_by,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
            entity.status.value,
            entity.kind.value,
        )
        async with self._write_context():
            try:
                await self._db.execute(_CONVERSATIONS_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db,
                    event=PERSISTENCE_CONVERSATION_FAILED,
                    operation="save",
                    conversation_id=entity.id,
                )
                msg = f"Constraint violation saving conversation {entity.id!r}"
                logger.warning(
                    PERSISTENCE_CONVERSATION_FAILED,
                    operation="save",
                    conversation_id=entity.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    event=PERSISTENCE_CONVERSATION_FAILED,
                    operation="save",
                    conversation_id=entity.id,
                )
                msg = f"Failed to save conversation {entity.id!r}"
                logger.warning(
                    PERSISTENCE_CONVERSATION_FAILED,
                    operation="save",
                    conversation_id=entity.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> Conversation | None:
        """Get a conversation by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"""
            SELECT {_CONVERSATION_COLUMNS}
            FROM conversations WHERE id = ?
        """  # noqa: S608 -- _CONVERSATION_COLUMNS is a fixed column list
        try:
            cursor = await self._db.execute(sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="get",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        conv = row_to_conversation(row)
        logger.debug(PERSISTENCE_CONVERSATION_FETCHED, conversation_id=entity_id)
        return conv

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """List conversations newest-first (``created_at DESC, id DESC``).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = f"""
            SELECT {_CONVERSATION_COLUMNS}
            FROM conversations
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- _CONVERSATION_COLUMNS is a fixed column list
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(row_to_conversation(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list conversations"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATION_LISTED, count=len(items))
        return items

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the conversation status.

        ``**updates`` accepts only ``updated_at`` (an ISO-8601 string);
        any other key raises ``QueryError``.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on mismatch or missing row.

        Raises:
            QueryError: On database errors or an unsupported update key.
        """
        unknown = set(updates) - _ALLOWED_TRANSITION_KEYS
        if unknown:
            msg = (
                "transition_if accepts only 'updated_at'; "
                f"got unsupported keys {sorted(unknown)!r}"
            )
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="transition_if",
                conversation_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        if "updated_at" in updates:
            sql = (
                "UPDATE conversations SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = ?"
            )
            params: tuple[object, ...] = (
                to_state.value,
                updates["updated_at"],
                entity_id,
                from_state.value,
            )
        else:
            sql = "UPDATE conversations SET status = ? WHERE id = ? AND status = ?"
            params = (to_state.value, entity_id, from_state.value)
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    event=PERSISTENCE_CONVERSATION_FAILED,
                    operation="transition_if",
                    conversation_id=entity_id,
                )
                msg = f"Failed to transition conversation {entity_id!r}"
                logger.warning(
                    PERSISTENCE_CONVERSATION_FAILED,
                    operation="transition_if",
                    conversation_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a conversation by id. ``True`` iff a row existed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database operation fails.
        """
        sql = "DELETE FROM conversations WHERE id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (entity_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    event=PERSISTENCE_CONVERSATION_FAILED,
                    operation="delete",
                    conversation_id=entity_id,
                )
                msg = f"Failed to delete conversation {entity_id!r}"
                logger.warning(
                    PERSISTENCE_CONVERSATION_FAILED,
                    operation="delete",
                    conversation_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0


__all__ = ["SQLiteConversationRepository"]
