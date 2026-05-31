"""SQLite repository for agent-initiated conversation invites.

Satisfies ``ConversationInviteRepository`` structurally: id-keyed CRUD,
atomic status compare-and-set (``PENDING -> ACCEPTED/DECLINED`` driven
by the human consent decision), and filtered queries by
``conversation_id`` / ``approval_id`` / ``target_agent_id`` / ``status``.
"""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from aiosqlite import Row

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_FAILED,
    COS_GROUP_INVITE_FETCHED,
    COS_GROUP_INVITE_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "id, conversation_id, approval_id, requested_by_agent_id, "
    "target_agent_id, target_role, reason, status, created_at"
)

_UPSERT_SQL = f"""
    INSERT INTO conversation_invites ({_SELECT_COLS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        conversation_id = excluded.conversation_id,
        approval_id = excluded.approval_id,
        requested_by_agent_id = excluded.requested_by_agent_id,
        target_agent_id = excluded.target_agent_id,
        target_role = excluded.target_role,
        reason = excluded.reason,
        status = excluded.status,
        created_at = excluded.created_at
"""  # noqa: S608  -- column list is a compile-time constant


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            COS_GROUP_INVITE_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


def _row_to_invite(row: Row) -> ConversationInvite:
    """Convert a database row into a :class:`ConversationInvite`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.

    Returns:
        Result of type ``ConversationInvite``.
    """
    try:
        target_role = row["target_role"]
        return ConversationInvite(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            approval_id=str(row["approval_id"]),
            requested_by_agent_id=str(row["requested_by_agent_id"]),
            target_agent_id=str(row["target_agent_id"]),
            target_role=str(target_role) if target_role is not None else None,
            reason=str(row["reason"]),
            status=ConversationInviteStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation invite row"
        logger.warning(
            COS_GROUP_INVITE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


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
        clauses.append("conversation_id = ?")
        params.append(filter_spec.conversation_id)
    if filter_spec.approval_id is not None:
        clauses.append("approval_id = ?")
        params.append(filter_spec.approval_id)
    if filter_spec.target_agent_id is not None:
        clauses.append("target_agent_id = ?")
        params.append(filter_spec.target_agent_id)
    if filter_spec.status is not None:
        clauses.append("status = ?")
        params.append(filter_spec.status.value)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


class SQLiteConversationInviteRepository:
    """SQLite-backed conversation invite repository.

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
            format_iso_utc(entity.created_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(self._db, operation="save", invite_id=entity.id)
                msg = f"Constraint violation saving invite {entity.id!r}"
                logger.warning(
                    COS_GROUP_INVITE_FAILED,
                    operation="save",
                    invite_id=entity.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="save", invite_id=entity.id)
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
            "WHERE id = ?"
        )
        try:
            cursor = await self._db.execute(sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        invite = _row_to_invite(row)
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
        sql = (
            f"SELECT {_SELECT_COLS} FROM conversation_invites "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(_row_to_invite(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = f"""
            SELECT {_SELECT_COLS} FROM conversation_invites
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608  -- ``where`` is a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(_row_to_invite(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = (
            "SELECT COUNT(*) FROM conversation_invites "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
            assert row is not None  # noqa: S101  -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        ``**updates`` must be empty (no status-correlated columns); any
        key raises ``QueryError``.

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
        sql = "UPDATE conversation_invites SET status = ? WHERE id = ? AND status = ?"
        params = (to_state.value, entity_id, from_state.value)
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    operation="transition_if",
                    invite_id=entity_id,
                )
                msg = f"Failed to transition invite {entity_id!r}"
                logger.warning(
                    COS_GROUP_INVITE_FAILED,
                    operation="transition_if",
                    invite_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an invite by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        sql = "DELETE FROM conversation_invites WHERE id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (entity_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="delete", invite_id=entity_id)
                msg = f"Failed to delete invite {entity_id!r}"
                logger.warning(
                    COS_GROUP_INVITE_FAILED,
                    operation="delete",
                    invite_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0


__all__ = ["SQLiteConversationInviteRepository"]
