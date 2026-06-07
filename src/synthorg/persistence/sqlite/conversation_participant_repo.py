"""SQLite repository for group-chat participant rosters.

``SQLiteConversationParticipantRepository`` backs the
``conversation_participants`` table: id-keyed CRUD, an atomic
``active`` <-> ``removed`` status compare-and-set, and a roster query
scoped to a conversation (optionally by status). Satisfies
``ConversationParticipantRepository`` structurally.
"""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_PARTICIPANT_FAILED,
    COS_GROUP_PARTICIPANT_FETCHED,
    COS_GROUP_PARTICIPANT_LISTED,
)
from synthorg.persistence._conversation_marshalling import row_to_participant
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset()

_PARTICIPANT_COLUMNS = (
    "id, conversation_id, agent_id, agent_name, participant_role, "
    "status, added_by, added_at"
)

_PARTICIPANT_UPSERT_SQL = """
    INSERT INTO conversation_participants (
        id, conversation_id, agent_id, agent_name, participant_role,
        status, added_by, added_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        conversation_id = excluded.conversation_id,
        agent_id = excluded.agent_id,
        agent_name = excluded.agent_name,
        participant_role = excluded.participant_role,
        status = excluded.status,
        added_by = excluded.added_by,
        added_at = excluded.added_at
"""


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure.

    A bare ``await db.rollback()`` in an ``except`` block can itself
    raise on the shared connection, masking the original domain error.
    ``MemoryError`` / ``RecursionError`` propagate unchanged.
    """
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            COS_GROUP_PARTICIPANT_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


class SQLiteConversationParticipantRepository:
    """SQLite-backed group-chat participant repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager serialising writes on the
            shared connection.
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

    async def save(self, entity: ConversationParticipant) -> None:
        """Upsert a participant row.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. a
                duplicate ``(conversation_id, agent_id)`` pair).
            QueryError: On other database errors.
        """
        params = (
            str(entity.id),
            entity.conversation_id,
            entity.agent_id,
            entity.agent_name,
            entity.participant_role,
            entity.status.value,
            entity.added_by,
            format_iso_utc(entity.added_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(_PARTICIPANT_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db, operation="save", conversation_id=entity.conversation_id
                )
                msg = (
                    "Constraint violation saving participant "
                    f"{entity.id!r} (conversation {entity.conversation_id!r})"
                )
                logger.warning(
                    COS_GROUP_PARTICIPANT_FAILED,
                    operation="save",
                    conversation_id=entity.conversation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="save", conversation_id=entity.conversation_id
                )
                msg = f"Failed to save participant {entity.id!r}"
                logger.warning(
                    COS_GROUP_PARTICIPANT_FAILED,
                    operation="save",
                    conversation_id=entity.conversation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ConversationParticipant | None:
        """Get a participant by id, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        sql = f"""
            SELECT {_PARTICIPANT_COLUMNS}
            FROM conversation_participants WHERE id = ?
        """  # noqa: S608 -- _PARTICIPANT_COLUMNS is a fixed column list
        try:
            cursor = await self._db.execute(sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch participant {entity_id!r}"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="get",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        participant = row_to_participant(row)
        logger.debug(
            COS_GROUP_PARTICIPANT_FETCHED,
            conversation_id=participant.conversation_id,
        )
        return participant

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationParticipantStatus,
        to_state: ConversationParticipantStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for participant membership status.

        Participants carry no status-correlated columns, so ``**updates``
        must be empty; any key raises ``QueryError``.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on mismatch or missing row.

        Raises:
            QueryError: On database errors or a non-empty ``updates``.
        """
        unknown = set(updates) - _ALLOWED_TRANSITION_KEYS
        if unknown:
            msg = (
                "transition_if accepts no update keys for participants; "
                f"got {sorted(unknown)!r}"
            )
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="transition_if",
                error=msg,
            )
            raise QueryError(msg)
        sql = (
            "UPDATE conversation_participants SET status = ? "
            "WHERE id = ? AND status = ?"
        )
        params = (to_state.value, entity_id, from_state.value)
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="transition_if")
                msg = f"Failed to transition participant {entity_id!r}"
                logger.warning(
                    COS_GROUP_PARTICIPANT_FAILED,
                    operation="transition_if",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a participant by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` otherwise.
        """
        sql = "DELETE FROM conversation_participants WHERE id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (entity_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="delete")
                msg = f"Failed to delete participant {entity_id!r}"
                logger.warning(
                    COS_GROUP_PARTICIPANT_FAILED,
                    operation="delete",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    def _filter_clauses(
        self, filter_spec: ConversationParticipantFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + params for *filter_spec*.

        Returns:
            A ``(where_sql, params)`` pair; ``where_sql`` is ``1=1`` when
            the spec is empty.
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.conversation_id is not None:
            clauses.append("conversation_id = ?")
            params.append(filter_spec.conversation_id)
        if filter_spec.status is not None:
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        where = " AND ".join(clauses) if clauses else "1=1"
        return where, params

    async def query(
        self,
        filter_spec: ConversationParticipantFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationParticipant, ...]:
        """Return participants matching the spec, oldest-first (paginated).

        Order is ``(added_at ASC, id ASC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=COS_GROUP_PARTICIPANT_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = self._filter_clauses(filter_spec)
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {_PARTICIPANT_COLUMNS}
            FROM conversation_participants WHERE {where}
            ORDER BY added_at ASC, id ASC
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- columns + predicates are closed sets
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(row_to_participant(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query conversation participants"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(COS_GROUP_PARTICIPANT_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: ConversationParticipantFilterSpec) -> int:
        """Count participants matching the filter spec.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The number of matching rows.
        """
        where, params = self._filter_clauses(filter_spec)
        sql = f"""
            SELECT COUNT(*) FROM conversation_participants WHERE {where}
        """  # noqa: S608 -- predicates are a closed set
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count conversation participants"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row is not None else 0


__all__ = ["SQLiteConversationParticipantRepository"]
