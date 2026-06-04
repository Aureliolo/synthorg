"""SQLite append-only repository for conversation turns."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONVERSATION_TURN_APPENDED,
    PERSISTENCE_CONVERSATION_TURN_FAILED,
    PERSISTENCE_CONVERSATION_TURN_QUERIED,
)
from synthorg.persistence._conversation_marshalling import row_to_turn
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.sqlite.conversation_repo._base import (
    _MAX_PAGE_LIMIT,
    _safe_rollback,
)

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

_TURN_INSERT_SQL = """
    INSERT INTO conversation_turns (
        id, conversation_id, sequence, role, content,
        author_agent_id, author_name, routed_topic, routing_confidence,
        created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_TURN_COLUMNS = (
    "id, conversation_id, sequence, role, content, "
    "author_agent_id, author_name, routed_topic, routing_confidence, created_at"
)

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
                    current.author_agent_id,
                    current.author_name,
                    current.routed_topic,
                    current.routing_confidence,
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

        Returns:
            The matching entities.

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
            SELECT {_TURN_COLUMNS}
            FROM conversation_turns WHERE {where}
            ORDER BY sequence DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608  -- ``where`` + columns are closed sets
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(row_to_turn(r) for r in rows)
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

        Returns:
            Numeric result of the operation.

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


__all__ = ["SQLiteConversationTurnRepository"]
