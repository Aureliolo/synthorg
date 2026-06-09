"""Postgres repository for group-chat participant rosters.

Sibling of the SQLite implementation, backed by
``psycopg_pool.AsyncConnectionPool``. ``TIMESTAMPTZ`` ``added_at``
returns a native ``datetime``; the shared coercer normalises it (and
any legacy ISO strings) to UTC-aware. Satisfies
``ConversationParticipantRepository`` structurally.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_PARTICIPANT_FAILED,
    COS_GROUP_PARTICIPANT_FETCHED,
    COS_GROUP_PARTICIPANT_LISTED,
)
from synthorg.persistence._conversation_marshalling import row_to_participant
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
)

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset()

_PARTICIPANT_COLUMNS = (
    "id, conversation_id, agent_id, agent_name, participant_role, "
    "status, added_by, added_at"
)

_PARTICIPANT_UPSERT_SQL = """
    INSERT INTO conversation_participants
        (id, conversation_id, agent_id, agent_name, participant_role,
         status, added_by, added_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        agent_id = EXCLUDED.agent_id,
        agent_name = EXCLUDED.agent_name,
        participant_role = EXCLUDED.participant_role,
        status = EXCLUDED.status,
        added_by = EXCLUDED.added_by,
        added_at = EXCLUDED.added_at
"""


class PostgresConversationParticipantRepository:
    """Postgres-backed group-chat participant repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
            entity.added_at,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_PARTICIPANT_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
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
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
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
        sql = (
            f"SELECT {_PARTICIPANT_COLUMNS} "  # noqa: S608 -- fixed column list
            "FROM conversation_participants WHERE id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            "UPDATE conversation_participants SET status = %s "
            "WHERE id = %s AND status = %s"
        )
        params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition participant {entity_id!r}"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="transition_if",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a participant by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` otherwise.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversation_participants WHERE id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete participant {entity_id!r}"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="delete",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted

    def _filter_clauses(
        self, filter_spec: ConversationParticipantFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + params for *filter_spec*.

        Returns:
            A ``(where_sql, params)`` pair; ``where_sql`` is ``TRUE`` when
            the spec is empty.
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.conversation_id is not None:
            clauses.append("conversation_id = %s")
            params.append(filter_spec.conversation_id)
        if filter_spec.status is not None:
            clauses.append("status = %s")
            params.append(filter_spec.status.value)
        where = " AND ".join(clauses) if clauses else "TRUE"
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
        sql = (
            f"SELECT {_PARTICIPANT_COLUMNS} "  # noqa: S608 -- closed column + predicate set
            f"FROM conversation_participants WHERE {where} "
            "ORDER BY added_at ASC, id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                items = tuple(row_to_participant(r) for r in rows)
        except psycopg.Error as exc:
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
        sql = (
            "SELECT COUNT(*) FROM conversation_participants "  # noqa: S608 -- closed predicate set
            f"WHERE {where}"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count conversation participants"
            logger.warning(
                COS_GROUP_PARTICIPANT_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row is not None else 0


__all__ = ["PostgresConversationParticipantRepository"]
