"""Postgres repositories for conversational clarify-and-propose state.

Siblings of the SQLite implementations, backed by
``psycopg_pool.AsyncConnectionPool``. ``TIMESTAMPTZ`` columns return
native ``datetime`` objects; the shared timestamp coercer normalises
them (and any legacy ISO strings) to UTC-aware values. Satisfy the
``conversation_protocol`` protocols structurally.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.observability import get_logger, safe_error_description
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
    validate_pagination_args,
)
from synthorg.persistence.conversation_protocol import (  # noqa: TC001
    ConversationTurnFilterSpec,
)

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"updated_at"})

_CONVERSATIONS_UPSERT_SQL = """
    INSERT INTO conversations (id, created_by, created_at, updated_at, status)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        created_by = EXCLUDED.created_by,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        status = EXCLUDED.status
"""

_TURN_INSERT_SQL = """
    INSERT INTO conversation_turns
        (id, conversation_id, sequence, role, content, created_at)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


def _row_to_conversation(row: dict[str, Any]) -> Conversation:
    """Convert a Postgres dict row into a :class:`Conversation`."""
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


def _row_to_turn(row: dict[str, Any]) -> ConversationTurn:
    """Convert a Postgres dict row into a :class:`ConversationTurn`."""
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


class PostgresConversationRepository:
    """Postgres-backed conversation header repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: Conversation) -> None:
        """Upsert a conversation header.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            entity.id,
            entity.created_by,
            entity.created_at,
            entity.updated_at,
            entity.status.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_CONVERSATIONS_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving conversation {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="save",
                conversation_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
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
        sql = (
            "SELECT id, created_by, created_at, updated_at, status "
            "FROM conversations WHERE id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, created_by, created_at, updated_at, status "
                    "FROM conversations ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_conversation(r) for r in rows)
        except psycopg.Error as exc:
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

        ``**updates`` accepts only ``updated_at``; any other key raises
        ``QueryError``.

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
                "UPDATE conversations SET status = %s, updated_at = %s "
                "WHERE id = %s AND status = %s"
            )
            params: tuple[object, ...] = (
                to_state.value,
                updates["updated_at"],
                entity_id,
                from_state.value,
            )
        else:
            sql = "UPDATE conversations SET status = %s WHERE id = %s AND status = %s"
            params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="transition_if",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a conversation by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversations WHERE id = %s", (entity_id,)
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="delete",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


class PostgresConversationTurnRepository:
    """Postgres-backed append-only conversation turn repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: ConversationTurn) -> None:
        """Append one turn (immutable once written).

        Raises:
            ConstraintViolationError: On constraint violations (e.g. a
                duplicate ``(conversation_id, sequence)``).
            QueryError: On other database errors.
        """
        params = (
            event.id,
            event.conversation_id,
            event.sequence,
            event.role.value,
            event.content,
            event.created_at,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_TURN_INSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = (
                "Constraint violation appending turn "
                f"{event.id!r} (conversation {event.conversation_id!r})"
            )
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="append",
                conversation_id=event.conversation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to append turn {event.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="append",
                conversation_id=event.conversation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_CONVERSATION_TURN_APPENDED,
            conversation_id=event.conversation_id,
            sequence=event.sequence,
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
            clauses.append("conversation_id = %s")
            params.append(filter_spec.conversation_id)
        where = " AND ".join(clauses) if clauses else "TRUE"
        params.extend([effective_limit, offset])
        sql = (
            "SELECT id, conversation_id, sequence, role, content, "  # noqa: S608
            f"created_at FROM conversation_turns WHERE {where} "
            "ORDER BY sequence DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                items = tuple(_row_to_turn(r) for r in rows)
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversation_turns WHERE created_at < %s",
                    (threshold,),
                )
                removed = max(0, cur.rowcount)
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge conversation turns"
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="purge_before",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed


__all__ = [
    "PostgresConversationRepository",
    "PostgresConversationTurnRepository",
]
