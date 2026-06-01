"""Postgres repository for agent-initiated conversation invites.

Sibling of ``SQLiteConversationInviteRepository`` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``ConversationInviteRepository`` structurally.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_FAILED,
    COS_GROUP_INVITE_FETCHED,
    COS_GROUP_INVITE_LISTED,
)
from synthorg.persistence._conversation_marshalling import row_to_invite
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    validate_pagination_args,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "id, conversation_id, approval_id, requested_by_agent_id, "
    "target_agent_id, target_role, reason, status, created_at"
)

_UPSERT_SQL = f"""
    INSERT INTO conversation_invites ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        approval_id = EXCLUDED.approval_id,
        requested_by_agent_id = EXCLUDED.requested_by_agent_id,
        target_agent_id = EXCLUDED.target_agent_id,
        target_role = EXCLUDED.target_role,
        reason = EXCLUDED.reason,
        status = EXCLUDED.status,
        created_at = EXCLUDED.created_at
"""  # noqa: S608  -- column list is a compile-time constant


def _build_where(
    filter_spec: ConversationInviteFilterSpec,
) -> tuple[str, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Returns:
        ``(where_clause, params)`` where ``where_clause`` is the SQL fragment (without
        the leading ``WHERE``) and ``params`` is the matching positional parameter list.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.conversation_id is not None:
        clauses.append("conversation_id = %s")
        params.append(filter_spec.conversation_id)
    if filter_spec.approval_id is not None:
        clauses.append("approval_id = %s")
        params.append(filter_spec.approval_id)
    if filter_spec.target_agent_id is not None:
        clauses.append("target_agent_id = %s")
        params.append(filter_spec.target_agent_id)
    if filter_spec.status is not None:
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


class PostgresConversationInviteRepository:
    """Postgres-backed conversation invite repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ConversationInvite) -> None:
        """Upsert a conversation invite.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            entity.id,
            entity.conversation_id,
            entity.approval_id,
            entity.requested_by_agent_id,
            entity.target_agent_id,
            entity.target_role,
            entity.reason,
            entity.status.value,
            entity.created_at,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving invite {entity.id!r}"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="save",
                invite_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save invite {entity.id!r}"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="save",
                invite_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ConversationInvite | None:
        """Get an invite by id, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM conversation_invites "  # noqa: S608
            "WHERE id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch invite {entity_id!r}"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="get",
                invite_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        invite = row_to_invite(row)
        logger.debug(COS_GROUP_INVITE_FETCHED, invite_id=entity_id)
        return invite

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationInvite, ...]:
        """List invites newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=COS_GROUP_INVITE_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} "  # noqa: S608
                    "FROM conversation_invites "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(row_to_invite(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to list invites"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(COS_GROUP_INVITE_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: ConversationInviteFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationInvite, ...]:
        """Return invites matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=COS_GROUP_INVITE_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = _build_where(filter_spec)
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} "  # noqa: S608
                    f"FROM conversation_invites WHERE {where} "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(row_to_invite(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to query invites"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(COS_GROUP_INVITE_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: ConversationInviteFilterSpec) -> int:
        """Count invites matching the filter spec.

        Raises:
            QueryError: If the database query fails.

        Returns:
            Number of matching rows.
        """
        where, params = _build_where(filter_spec)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "SELECT COUNT(*) FROM conversation_invites "  # noqa: S608
                    f"WHERE {where}",
                    params,
                )
                row = await cur.fetchone()
                assert row is not None  # noqa: S101  -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count invites"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationInviteStatus,
        to_state: ConversationInviteStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the invite status.

        ``**updates`` must be empty; any key raises ``QueryError``.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on mismatch or missing row.

        Raises:
            QueryError: On database errors or a non-empty ``updates``.
        """
        if updates:
            msg = (
                "transition_if does not accept status-correlated "
                f"columns; got keys {sorted(updates)!r}"
            )
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="transition_if",
                invite_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        sql = (
            "UPDATE conversation_invites SET status = %s WHERE id = %s AND status = %s"
        )
        params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition invite {entity_id!r}"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="transition_if",
                invite_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an invite by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversation_invites WHERE id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete invite {entity_id!r}"
            logger.warning(
                COS_GROUP_INVITE_FAILED,
                operation="delete",
                invite_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


__all__ = ["PostgresConversationInviteRepository"]
