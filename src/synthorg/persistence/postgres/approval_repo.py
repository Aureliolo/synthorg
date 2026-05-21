"""Postgres repository implementation for approval items.

Sibling of :class:`SQLiteApprovalRepository` backed by
``psycopg_pool.AsyncConnectionPool``.  Uses native ``JSONB`` for the
``evidence_package`` and ``metadata`` columns and ``TIMESTAMPTZ`` for
all timestamps -- matching the schema in
``persistence/postgres/schema.sql``.

Callers depend on the :class:`ApprovalRepository` Protocol from
``persistence/approval_protocol.py``; this class satisfies it
structurally.
"""

from datetime import datetime  # noqa: TC003 -- runtime param type
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.evidence import EvidencePackage
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_FAILED,
    API_APPROVAL_REPO_FETCHED,
    API_APPROVAL_REPO_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, validate_pagination_args
from synthorg.persistence.approval_protocol import ApprovalFilterSpec  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "id, action_type, title, description, requested_by, risk_level, "
    "source, status, created_at, expires_at, decided_at, decided_by, "
    "decision_reason, task_id, evidence_package, metadata, consumed_at"
)

_APPROVALS_UPSERT_SQL = f"""
    INSERT INTO approvals ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        action_type = EXCLUDED.action_type,
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        requested_by = EXCLUDED.requested_by,
        risk_level = EXCLUDED.risk_level,
        source = EXCLUDED.source,
        status = EXCLUDED.status,
        expires_at = EXCLUDED.expires_at,
        decided_at = EXCLUDED.decided_at,
        decided_by = EXCLUDED.decided_by,
        decision_reason = EXCLUDED.decision_reason,
        task_id = EXCLUDED.task_id,
        evidence_package = EXCLUDED.evidence_package,
        metadata = EXCLUDED.metadata,
        consumed_at = COALESCE(approvals.consumed_at, EXCLUDED.consumed_at)
"""  # noqa: S608 -- column list is compile-time constant


def _row_to_item(row: dict[str, Any]) -> ApprovalItem:
    """Convert a Postgres dict row into an :class:`ApprovalItem`.

    Postgres ``TIMESTAMPTZ`` columns return native ``datetime``
    objects via psycopg, but legacy or migrated rows may carry ISO
    8601 strings; the function dispatches on ``isinstance(..., str)``
    and parses string values via :func:`parse_iso_utc` (strict on
    naive) so both representations land as UTC-aware datetimes.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        # Normalise only NULL explicitly; preserve other falsy payloads
        # (e.g. ``[]``, ``""``, ``0``, ``false``) so ``ApprovalItem``'s
        # ``dict[str, str]`` validation rejects them via ``ValidationError``
        # rather than masking corruption as an empty dict.
        raw_metadata = row["metadata"]
        metadata_raw = {} if raw_metadata is None else raw_metadata
        # Postgres JSONB always deserializes to dict/list/primitive; if a
        # legacy row stored a non-object value, ``ApprovalItem`` construction
        # below will raise ``ValidationError`` and the outer except wraps it.
        evidence_package = (
            EvidencePackage.model_validate(row["evidence_package"])
            if row["evidence_package"] is not None
            else None
        )
        # Postgres ``TIMESTAMPTZ`` columns normally return tz-aware
        # ``datetime`` objects via psycopg, but the offset reflects the
        # session timezone -- normalize to UTC so reads are symmetric
        # with writes regardless of the connection's ``SET TIME ZONE``
        # setting.  Legacy or migrated rows may still arrive as ISO
        # strings; the shared dispatcher tolerates both.
        created_at = coerce_row_timestamp(row["created_at"])
        expires_at = (
            coerce_row_timestamp(row["expires_at"])
            if row["expires_at"] is not None
            else None
        )
        decided_at = (
            coerce_row_timestamp(row["decided_at"])
            if row["decided_at"] is not None
            else None
        )
        consumed_at = (
            coerce_row_timestamp(row["consumed_at"])
            if row["consumed_at"] is not None
            else None
        )
        return ApprovalItem(
            id=str(row["id"]),
            action_type=str(row["action_type"]),
            title=str(row["title"]),
            description=str(row["description"]),
            requested_by=str(row["requested_by"]),
            risk_level=ApprovalRiskLevel(str(row["risk_level"])),
            source=ApprovalSource(str(row["source"])),
            status=ApprovalStatus(str(row["status"])),
            created_at=created_at,
            expires_at=expires_at,
            decided_at=decided_at,
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            decision_reason=(
                str(row["decision_reason"])
                if row["decision_reason"] is not None
                else None
            ),
            task_id=(str(row["task_id"]) if row["task_id"] is not None else None),
            consumed_at=consumed_at,
            evidence_package=evidence_package,
            metadata=metadata_raw,
        )
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        try:
            row_id = str(row["id"]) if row else "<unknown>"
        except TypeError, KeyError:
            row_id = "<unknown>"
        msg = f"Failed to parse approval row {row_id!r}: {safe_error_description(exc)}"
        logger.warning(
            API_APPROVAL_REPO_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class PostgresApprovalRepository:
    """Postgres-backed approval item repository.

    Provides CRUD operations for approval items using a shared
    ``psycopg_pool.AsyncConnectionPool``.  Satisfies the
    :class:`ApprovalRepository` protocol structurally.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, item: ApprovalItem) -> None:
        """Upsert an approval item.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        evidence_json = (
            Jsonb(item.evidence_package.model_dump(mode="json"))
            if item.evidence_package is not None
            else None
        )
        params = (
            item.id,
            item.action_type,
            item.title,
            item.description,
            item.requested_by,
            item.risk_level.value,
            item.source.value,
            item.status.value,
            item.created_at,
            item.expires_at,
            item.decided_at,
            item.decided_by,
            item.decision_reason,
            item.task_id,
            evidence_json,
            Jsonb(item.metadata),
            item.consumed_at,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_APPROVALS_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            # Extract the PostgreSQL constraint name so callers can
            # dispatch reliably on
            # :attr:`ConstraintViolationError.constraint` without
            # parsing server error text.
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving approval {item.id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=item.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(
                msg,
                constraint=constraint,
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save approval {item.id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=item.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def save_many(self, items: Sequence[ApprovalItem]) -> None:
        """Upsert multiple approval items in a single transaction.

        Empty input is a no-op.  Single-item input falls back to
        :meth:`save` so the per-item error context still names the
        offending id on constraint violation.
        """
        if not items:
            return
        if len(items) == 1:
            await self.save(items[0])
            return
        param_rows = []
        for item in items:
            evidence_json = (
                Jsonb(item.evidence_package.model_dump(mode="json"))
                if item.evidence_package is not None
                else None
            )
            param_rows.append(
                (
                    item.id,
                    item.action_type,
                    item.title,
                    item.description,
                    item.requested_by,
                    item.risk_level.value,
                    item.source.value,
                    item.status.value,
                    item.created_at,
                    item.expires_at,
                    item.decided_at,
                    item.decided_by,
                    item.decision_reason,
                    item.task_id,
                    evidence_json,
                    Jsonb(item.metadata),
                    item.consumed_at,
                ),
            )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.executemany(_APPROVALS_UPSERT_SQL, param_rows)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving approval batch (size={len(items)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(items),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save approval batch (size={len(items)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(items),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def expire_if_pending(
        self, ids: Sequence[NotBlankStr]
    ) -> tuple[NotBlankStr, ...]:
        """Compare-and-set: flip rows still PENDING to EXPIRED.

        Uses ``UPDATE ... WHERE id = ANY(%s) AND status='pending'
        RETURNING id`` so the compare-and-set is atomic at the row
        level and the returned ids reflect what actually transitioned.
        """
        if not ids:
            return ()
        sql = (
            "UPDATE approvals SET status = %s "
            "WHERE id = ANY(%s) "
            "AND status = %s "
            "RETURNING id"
        )
        params = (
            ApprovalStatus.EXPIRED.value,
            list(ids),
            ApprovalStatus.PENDING.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to expire approval batch (size={len(ids)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(ids),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(NotBlankStr(row[0]) for row in rows)

    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None:
        """Get an approval item by ID, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM approvals WHERE id = %s"  # noqa: S608
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
        item = _row_to_item(row)
        logger.debug(API_APPROVAL_REPO_FETCHED, approval_id=approval_id)
        return item

    async def get_many(self, ids: Sequence[NotBlankStr]) -> tuple[ApprovalItem, ...]:
        """Batch-fetch approval items by id via ``WHERE id = ANY(%s)``.

        Empty input short-circuits to ``()`` without issuing SQL.
        Missing ids are simply absent from the result.
        """
        if not ids:
            return ()
        sql = f"SELECT {_SELECT_COLS} FROM approvals WHERE id = ANY(%s)"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (list(ids),))
                rows = await cur.fetchall()
                items = tuple(_row_to_item(r) for r in rows)
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
            QueryError: If the database query fails or
                pagination args are invalid.
        """
        effective_limit = validate_pagination_args(
            limit,
            offset,
            event=API_APPROVAL_REPO_FAILED,
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM approvals "  # noqa: S608
                    "ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_item(r) for r in rows)
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
            QueryError: If the database query fails or
                pagination args are invalid.
        """
        effective_limit = validate_pagination_args(
            limit,
            offset,
            event=API_APPROVAL_REPO_FAILED,
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        clauses: list[str] = []
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
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM approvals "  # noqa: S608
                    f"WHERE {where_sql} ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_item(r) for r in rows)
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
        clauses: list[str] = []
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

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ApprovalStatus,
        to_state: ApprovalStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for approval state transitions.

        Transitions the approval from ``from_state`` to ``to_state`` iff
        the current persisted status matches ``from_state``. Returns ``True``
        iff the state transition succeeded.

        Decision metadata (``decided_at`` / ``decided_by`` /
        ``decision_json``) is governed by a table CHECK constraint that
        requires the full triple together, so partial writes through
        this method are rejected rather than silently dropped: pass an
        empty ``updates`` and persist the decision triple via the
        dedicated decision path.

        Args:
            entity_id: The approval id.
            from_state: Expected current status.
            to_state: Target status.
            **updates: Must be empty; any keys raise ``QueryError``.

        Returns:
            ``True`` iff the transition succeeded, ``False`` on state
            mismatch or when no row exists.

        Raises:
            QueryError: On database errors, or if ``updates`` is
                non-empty (status-correlated writes are not supported
                through this CAS path).
        """
        if updates:
            msg = (
                "transition_if does not persist decision metadata "
                f"(got keys {sorted(updates)!r}); the approvals CHECK "
                "constraint requires the full decision triple, so use "
                "the dedicated decision path instead"
            )
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        sql = "UPDATE approvals SET status = %s WHERE id = %s AND status = %s"
        params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition approval {entity_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def consume_if_approved(
        self,
        approval_id: NotBlankStr,
        *,
        consumed_at: datetime,
    ) -> bool:
        """Atomic compare-and-set: mark an APPROVED grant as consumed.

        Sets ``consumed_at`` iff the row is currently ``approved`` and not
        already consumed, so a one-shot approval can authorise exactly one
        action. Returns ``True`` iff this call won the race (rowcount == 1);
        ``False`` on replay (already consumed), state mismatch (not
        approved), or missing row.

        Args:
            approval_id: The approval id.
            consumed_at: Aware UTC timestamp to stamp on success.

        Returns:
            ``True`` iff the grant was consumed by this call.

        Raises:
            QueryError: On database errors.
        """
        sql = (
            "UPDATE approvals SET consumed_at = %s "
            "WHERE id = %s AND status = %s AND consumed_at IS NULL"
        )
        params = (consumed_at, approval_id, ApprovalStatus.APPROVED.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                consumed = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to consume approval {approval_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return consumed

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Delete an approval item; returns True when a row was removed.

        Raises:
            QueryError: If the database operation fails.
        """
        sql = "DELETE FROM approvals WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (approval_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete approval {approval_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
