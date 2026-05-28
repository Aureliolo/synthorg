"""Postgres repository for conversational work proposals.

Sibling of ``SQLiteConversationalProposalRepository`` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``ConversationalProposalRepository`` structurally.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from synthorg.core.enums import ConversationalProposalStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
    PERSISTENCE_CONVERSATIONAL_PROPOSAL_FETCHED,
    PERSISTENCE_CONVERSATIONAL_PROPOSAL_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    validate_pagination_args,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
)

if TYPE_CHECKING:
    from typing import Any

    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = "id, conversation_id, approval_id, work_item_json, status, created_at"

_UPSERT_SQL = f"""
    INSERT INTO conversational_proposals ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        approval_id = EXCLUDED.approval_id,
        work_item_json = EXCLUDED.work_item_json,
        status = EXCLUDED.status,
        created_at = EXCLUDED.created_at
"""  # noqa: S608  -- column list is a compile-time constant


def _row_to_proposal(row: dict[str, Any]) -> ConversationalProposal:
    """Convert a Postgres dict row into a :class:`ConversationalProposal`.

    Returns:
        Result of type ``ConversationalProposal``.

    Raises:
        QueryError: If the database query fails.
    """
    try:
        return ConversationalProposal(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            approval_id=str(row["approval_id"]),
            work_item_json=str(row["work_item_json"]),
            status=ConversationalProposalStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversational proposal row"
        logger.warning(
            PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _build_where(
    filter_spec: ConversationalProposalFilterSpec,
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
    if filter_spec.status is not None:
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


class PostgresConversationalProposalRepository:
    """Postgres-backed conversational proposal repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ConversationalProposal) -> None:
        """Upsert a conversational proposal.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            entity.id,
            entity.conversation_id,
            entity.approval_id,
            entity.work_item_json,
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
            msg = f"Constraint violation saving proposal {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="save",
                proposal_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save proposal {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="save",
                proposal_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ConversationalProposal | None:
        """Get a proposal by id, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM conversational_proposals "  # noqa: S608
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
            msg = f"Failed to fetch proposal {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="get",
                proposal_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        proposal = _row_to_proposal(row)
        logger.debug(PERSISTENCE_CONVERSATIONAL_PROPOSAL_FETCHED, proposal_id=entity_id)
        return proposal

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        """List proposals newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} "  # noqa: S608
                    "FROM conversational_proposals "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_proposal(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to list proposals"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATIONAL_PROPOSAL_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        """Return proposals matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED
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
                    f"FROM conversational_proposals WHERE {where} "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_proposal(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to query proposals"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATIONAL_PROPOSAL_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        """Count proposals matching the filter spec.

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
                    "SELECT COUNT(*) FROM conversational_proposals "  # noqa: S608
                    f"WHERE {where}",
                    params,
                )
                row = await cur.fetchone()
                assert row is not None  # noqa: S101  -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count proposals"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the proposal status.

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
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="transition_if",
                proposal_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        sql = (
            "UPDATE conversational_proposals SET status = %s "
            "WHERE id = %s AND status = %s"
        )
        params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition proposal {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="transition_if",
                proposal_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a proposal by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversational_proposals WHERE id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete proposal {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED,
                operation="delete",
                proposal_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


__all__ = ["PostgresConversationalProposalRepository"]
