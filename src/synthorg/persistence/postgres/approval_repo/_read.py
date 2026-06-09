"""Read-path mixin for the Postgres approval repository."""

from collections.abc import Sequence
from typing import LiteralString

import psycopg
from psycopg.rows import dict_row

from synthorg.core.approval import ApprovalItem
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_FAILED,
    API_APPROVAL_REPO_FETCHED,
    API_APPROVAL_REPO_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.approval_protocol import ApprovalFilterSpec
from synthorg.persistence.postgres.approval_repo._base import _ApprovalRepoBase
from synthorg.persistence.postgres.approval_repo._marshalling import row_to_item
from synthorg.persistence.postgres.approval_repo._sql import SELECT_COLS

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000


def _filter_clauses(
    filter_spec: ApprovalFilterSpec,
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE predicate + params from an approval filter spec.

    Returns:
        ``(where_sql, params)`` where ``where_sql`` is ``"TRUE"`` when no
        filter applies. The predicate is ``LiteralString`` (only literal
        column fragments are ever appended; every value goes through the
        params list), so callers can interpolate it without weakening the
        SQL-injection guarantee.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    if filter_spec.risk_level is not None:
        clauses.append("risk_level = %s")
        params.append(filter_spec.risk_level.value)
    if filter_spec.action_type is not None:
        clauses.append("action_type = %s")
        params.append(filter_spec.action_type)
    where_sql = " AND ".join(clauses) if clauses else "TRUE"
    return where_sql, params


class _ReadMixin(_ApprovalRepoBase):
    """Fetch / list / query / count operations for approval items."""

    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None:
        """Get an approval item by ID, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {SELECT_COLS} FROM approvals WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (approval_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch approval {approval_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        item = row_to_item(row)
        logger.debug(API_APPROVAL_REPO_FETCHED, approval_id=approval_id)
        return item

    async def get_many(self, ids: Sequence[NotBlankStr]) -> tuple[ApprovalItem, ...]:
        """Batch-fetch approval items by id via ``WHERE id = ANY(%s)``.

        Empty input short-circuits to ``()`` without issuing SQL.
        Missing ids are simply absent from the result.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if not ids:
            return ()
        sql = f"SELECT {SELECT_COLS} FROM approvals WHERE id = ANY(%s)"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (list(ids),))
                rows = await cur.fetchall()
                items = tuple(row_to_item(r) for r in rows)
        except psycopg.Error as exc:
            msg = f"Failed to batch-fetch approvals (size={len(ids)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(ids),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(API_APPROVAL_REPO_LISTED, count=len(items))
        return items

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List all approval items (paginated, newest-first).

        Results are ordered by ``(created_at DESC, id DESC)``.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Approval items in descending creation order.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=API_APPROVAL_REPO_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {SELECT_COLS} FROM approvals "  # noqa: S608
                    "ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(row_to_item(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to list approvals"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(API_APPROVAL_REPO_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: ApprovalFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items matching the filter spec (paginated).

        Results are ordered by ``(created_at DESC, id DESC)``.

        Args:
            filter_spec: Carries optional status, risk_level, action_type
                filters (all optional).
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching approval items in descending creation order.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=API_APPROVAL_REPO_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where_sql, params = _filter_clauses(filter_spec)
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {SELECT_COLS} FROM approvals "  # noqa: S608
                    f"WHERE {where_sql} ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(row_to_item(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to query approvals"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(API_APPROVAL_REPO_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: ApprovalFilterSpec) -> int:
        """Count approval items matching the filter spec.

        Args:
            filter_spec: Carries optional status, risk_level, action_type
                filters (all optional).

        Returns:
            Count of matching approval items.

        Raises:
            QueryError: If the database query fails.
        """
        where_sql, params = _filter_clauses(filter_spec)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    f"SELECT COUNT(*) FROM approvals WHERE {where_sql}",  # noqa: S608
                    params,
                )
                row = await cur.fetchone()
                assert row is not None  # noqa: S101  -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count approvals"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc


__all__ = ["_ReadMixin"]
