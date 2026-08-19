"""Postgres conversation-header repository.

Id-keyed CRUD plus the atomic status compare-and-set, so two concurrent
turns on one conversation cannot both drive the ``ACTIVE -> PROPOSED``
transition. Satisfies ``ConversationRepository`` structurally.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Conversation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.conversation import (
    PERSISTENCE_CONVERSATION_FAILED,
    PERSISTENCE_CONVERSATION_FETCHED,
    PERSISTENCE_CONVERSATION_LISTED,
)
from synthorg.persistence._conversation_marshalling import row_to_conversation
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.postgres._integrity import raise_constraint_violation

logger = get_logger(__name__)

_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"updated_at"})

_CONVERSATIONS_UPSERT_SQL = """
    INSERT INTO conversations
        (id, created_by, created_at, updated_at, status, kind)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        created_by = EXCLUDED.created_by,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        status = EXCLUDED.status,
        kind = EXCLUDED.kind
"""

_CONVERSATION_COLUMNS = "id, created_by, created_at, updated_at, status, kind"


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
            str(entity.id),
            entity.created_by,
            entity.created_at,
            entity.updated_at,
            entity.status.value,
            entity.kind.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_CONVERSATIONS_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = f"Constraint violation saving conversation {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="save",
                conversation_id=str(entity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise_constraint_violation(exc, msg)
        except psycopg.Error as exc:
            msg = f"Failed to save conversation {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="save",
                conversation_id=str(entity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> Conversation | None:
        """Get a conversation by id, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        sql = f"SELECT {_CONVERSATION_COLUMNS} FROM conversations WHERE id = %s"  # noqa: S608 -- fixed column list
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
        conv = row_to_conversation(row)
        logger.debug(PERSISTENCE_CONVERSATION_FETCHED, conversation_id=entity_id)
        return conv

    async def list_items(
        self,
        *,
        created_by: NotBlankStr | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """List conversations newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities, scoped to ``created_by`` when set.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_FAILED
        )
        effective_limit = min(effective_limit, MAX_PAGE_SIZE)
        where = "WHERE created_by = %s " if created_by is not None else ""
        params: tuple[object, ...] = (
            (created_by, effective_limit, offset)
            if created_by is not None
            else (effective_limit, offset)
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_CONVERSATION_COLUMNS} "  # noqa: S608 -- fixed cols + WHERE
                    f"FROM conversations {where}"
                    "ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(row_to_conversation(r) for r in rows)
        except QueryError:
            raise
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

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
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


__all__ = ["PostgresConversationRepository"]
