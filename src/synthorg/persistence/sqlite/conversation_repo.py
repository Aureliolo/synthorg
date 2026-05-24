"""SQLite repositories for conversational clarify-and-propose state.

``SQLiteConversationRepository`` backs the conversation header
(id-keyed CRUD + atomic status compare-and-set);
``SQLiteConversationTurnRepository`` backs the immutable ordered
turns (append + filtered query + retention purge). Both satisfy the
protocols in ``persistence/conversation_protocol.py`` structurally.
"""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from aiosqlite import Row

from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONVERSATION_FAILED,
    PERSISTENCE_CONVERSATION_FETCHED,
    PERSISTENCE_CONVERSATION_LISTED,
    PERSISTENCE_CONVERSATION_TURN_APPENDED,
    PERSISTENCE_CONVERSATION_TURN_FAILED,
    PERSISTENCE_CONVERSATION_TURN_QUERIED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.conversation_protocol import (  # noqa: TC001
    ConversationTurnFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"updated_at"})

_CONVERSATIONS_UPSERT_SQL = """
    INSERT INTO conversations (
        id, created_by, created_at, updated_at, status
    ) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        created_by = excluded.created_by,
        created_at = excluded.created_at,
        updated_at = excluded.updated_at,
        status = excluded.status
"""

_TURN_INSERT_SQL = """
    INSERT INTO conversation_turns (
        id, conversation_id, sequence, role, content, created_at
    ) VALUES (?, ?, ?, ?, ?, ?)
"""

_TURN_NEXT_SEQUENCE_SQL = """
    SELECT COALESCE(MAX(sequence), -1) + 1 FROM conversation_turns
    WHERE conversation_id = ?
"""

# Bounded retry on the (conversation_id, sequence) uniqueness race.
# Two concurrent ``converse()`` calls can both compute the same
# sequence from a stale read and the second insert will collide. We
# re-query the live max sequence and retry the insert; with a small
# bound any caller losing repeatedly is a sign of write-side
# contention worth surfacing as a constraint violation.
_TURN_APPEND_MAX_RETRIES: int = 3
# Substring that flags the (conversation_id, sequence) uniqueness
# violation in SQLite's IntegrityError message ("UNIQUE constraint
# failed: conversation_turns.conversation_id,
# conversation_turns.sequence"). The constraint is named
# ``uq_ct_conversation_sequence`` in the schema but SQLite reports
# columns rather than the name; matching on the column-pair string
# keeps this check tied to the specific constraint without coupling
# to the named-constraint output format.
_TURN_SEQUENCE_UNIQUE_HINT: str = "conversation_turns.sequence"


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    event: str,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure.

    A bare ``await db.rollback()`` in an ``except`` block can itself
    raise on the shared connection, masking the original domain error.
    This helper logs the rollback failure under its own structured
    event and swallows it so the caller can re-raise the root cause.
    ``MemoryError`` / ``RecursionError`` propagate unchanged.
    """
    try:
        await db.rollback()
    except MemoryError, RecursionError:
        raise
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            event,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


def _row_to_conversation(row: Row) -> Conversation:
    """Convert a database row into a :class:`Conversation`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return Conversation(
            id=str(row["id"]),
            created_by=str(row["created_by"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            status=ConversationStatus(str(row["status"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation row"
        logger.warning(
            PERSISTENCE_CONVERSATION_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _row_to_turn(row: Row) -> ConversationTurn:
    """Convert a database row into a :class:`ConversationTurn`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ConversationTurn(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            sequence=int(row["sequence"]),
            role=ConversationRole(str(row["role"])),
            content=str(row["content"]),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation turn row"
        logger.warning(
            PERSISTENCE_CONVERSATION_TURN_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


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

        Raises:
            QueryError: If the database query fails.
        """
        sql = """
            SELECT id, created_by, created_at, updated_at, status
            FROM conversations WHERE id = ?
        """
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
        conv = _row_to_conversation(row)
        logger.debug(PERSISTENCE_CONVERSATION_FETCHED, conversation_id=entity_id)
        return conv

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """List conversations newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = """
            SELECT id, created_by, created_at, updated_at, status
            FROM conversations
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(_row_to_conversation(r) for r in rows)
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


class SQLiteConversationTurnRepository:
    """SQLite-backed append-only conversation turn repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
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

    async def append(self, event: ConversationTurn) -> None:
        """Append one turn (immutable once written).

        Sequence collisions on ``(conversation_id, sequence)`` are a
        natural TOCTOU race when two concurrent callers compute the
        next sequence from a stale snapshot; this method re-queries
        the live max sequence and retries the insert up to
        ``_TURN_APPEND_MAX_RETRIES`` times before surfacing the
        violation. Other constraint failures (FK miss, CHECK on
        content/role/created_at) are not retried and translate
        directly to ``ConstraintViolationError``.

        Raises:
            ConstraintViolationError: On non-sequence constraint
                violations, or a sequence collision that still
                conflicts after the retry budget.
            QueryError: On other database errors.
        """
        current = event
        async with self._write_context():
            for attempt in range(_TURN_APPEND_MAX_RETRIES + 1):
                params = (
                    current.id,
                    current.conversation_id,
                    current.sequence,
                    current.role.value,
                    current.content,
                    format_iso_utc(current.created_at),
                )
                try:
                    await self._db.execute(_TURN_INSERT_SQL, params)
                    await self._db.commit()
                    break
                except sqlite3.IntegrityError as exc:
                    await _safe_rollback(
                        self._db,
                        event=PERSISTENCE_CONVERSATION_TURN_FAILED,
                        operation="append",
                        conversation_id=current.conversation_id,
                    )
                    sequence_race = (
                        _TURN_SEQUENCE_UNIQUE_HINT in str(exc)
                        and attempt < _TURN_APPEND_MAX_RETRIES
                    )
                    if sequence_race:
                        try:
                            cursor = await self._db.execute(
                                _TURN_NEXT_SEQUENCE_SQL,
                                (current.conversation_id,),
                            )
                            row = await cursor.fetchone()
                            next_sequence = int(row[0]) if row is not None else 0
                        except (sqlite3.Error, aiosqlite.Error) as resequence_exc:
                            msg = (
                                "Failed to resolve next sequence while "
                                f"appending turn {current.id!r} "
                                f"(conversation {current.conversation_id!r})"
                            )
                            logger.warning(
                                PERSISTENCE_CONVERSATION_TURN_FAILED,
                                operation="append",
                                phase="resequence",
                                conversation_id=current.conversation_id,
                                error_type=type(resequence_exc).__name__,
                                error=safe_error_description(resequence_exc),
                            )
                            raise QueryError(msg) from resequence_exc
                        current = current.model_copy(
                            update={"sequence": next_sequence},
                        )
                        continue
                    msg = (
                        "Constraint violation appending turn "
                        f"{current.id!r} "
                        f"(conversation {current.conversation_id!r})"
                    )
                    logger.warning(
                        PERSISTENCE_CONVERSATION_TURN_FAILED,
                        operation="append",
                        conversation_id=current.conversation_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise ConstraintViolationError(msg, constraint=str(exc)) from exc
                except (sqlite3.Error, aiosqlite.Error) as exc:
                    await _safe_rollback(
                        self._db,
                        event=PERSISTENCE_CONVERSATION_TURN_FAILED,
                        operation="append",
                        conversation_id=current.conversation_id,
                    )
                    msg = f"Failed to append turn {current.id!r}"
                    logger.warning(
                        PERSISTENCE_CONVERSATION_TURN_FAILED,
                        operation="append",
                        conversation_id=current.conversation_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_CONVERSATION_TURN_APPENDED,
            conversation_id=current.conversation_id,
            sequence=current.sequence,
        )

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        """Return turns matching the spec, newest-first (paginated).

        Order is ``(sequence DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_TURN_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.conversation_id is not None:
            clauses.append("conversation_id = ?")
            params.append(filter_spec.conversation_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT id, conversation_id, sequence, role, content, created_at
            FROM conversation_turns WHERE {where}
            ORDER BY sequence DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608  -- ``where`` is a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(_row_to_turn(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query conversation turns"
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATION_TURN_QUERIED, count=len(items))
        return items

    async def purge_before(self, threshold: datetime) -> int:
        """Delete turns created before ``threshold``. Returns rows removed.

        Raises:
            QueryError: On database errors.
        """
        sql = "DELETE FROM conversation_turns WHERE created_at < ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (format_iso_utc(threshold),))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    event=PERSISTENCE_CONVERSATION_TURN_FAILED,
                    operation="purge_before",
                )
                msg = "Failed to purge conversation turns"
                logger.warning(
                    PERSISTENCE_CONVERSATION_TURN_FAILED,
                    operation="purge_before",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return max(0, cursor.rowcount)


__all__ = [
    "SQLiteConversationRepository",
    "SQLiteConversationTurnRepository",
]
