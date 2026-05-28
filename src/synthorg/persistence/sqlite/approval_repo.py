# module-kind: complex_service
"""SQLite repository implementation for approval items.

One cohesive responsibility: persist approval items + their evidence
packages on SQLite. The class bundles CRUD, filter-spec queries,
JSON serialisation, and safe-rollback error handling, all sharing
the same write-context and row-to-model helpers.
"""

import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite
from aiosqlite import Row
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.evidence import EvidencePackage
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_FAILED,
    API_APPROVAL_REPO_FETCHED,
    API_APPROVAL_REPO_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.approval_protocol import ApprovalFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_APPROVALS_UPSERT_SQL = """
    INSERT INTO approvals (
        id, action_type, title, description, requested_by,
        risk_level, source, status, created_at, expires_at,
        decided_at, decided_by, decision_reason,
        task_id, evidence_package, metadata, consumed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        action_type = excluded.action_type,
        title = excluded.title,
        description = excluded.description,
        requested_by = excluded.requested_by,
        risk_level = excluded.risk_level,
        source = excluded.source,
        status = excluded.status,
        expires_at = excluded.expires_at,
        decided_at = excluded.decided_at,
        decided_by = excluded.decided_by,
        decision_reason = excluded.decision_reason,
        task_id = excluded.task_id,
        evidence_package = excluded.evidence_package,
        metadata = excluded.metadata,
        consumed_at = COALESCE(approvals.consumed_at, excluded.consumed_at)
"""


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure.

    Bare ``await db.rollback()`` calls in ``except`` blocks can themselves
    raise (the connection that prompted the rollback is often the same
    one that's about to be torn down). When that happens, the rollback
    exception unwinds past the original error, and callers receive a
    raw ``sqlite3.Error`` instead of the contracted domain error
    (``ConstraintViolationError`` / ``QueryError``). This helper logs
    the rollback failure under its own structured event and swallows
    it so the original exception can be re-raised by the caller.
    ``MemoryError`` / ``RecursionError`` propagate unchanged.

    ``operation`` names the caller (``save`` / ``save_many`` /
    ``delete``) so dashboards can attribute rollback failures; it
    lands in the ``operation`` log field, distinct from the fixed
    ``phase="rollback"`` field this helper sets unconditionally. The
    keyword-only ``operation`` parameter prevents the previous shape
    from colliding with the hardcoded ``phase`` kwarg in **log_context.
    """
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            API_APPROVAL_REPO_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


def _row_to_item(row: Row) -> ApprovalItem:
    """Convert a database row to an ApprovalItem.

    Args:
        row: A row from aiosqlite with ``row_factory = aiosqlite.Row``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.

    Returns:
        Result of type ``ApprovalItem``.
    """
    try:
        metadata_raw: dict[str, str] = json.loads(str(row["metadata"]))
        return ApprovalItem(
            id=str(row["id"]),
            action_type=str(row["action_type"]),
            title=str(row["title"]),
            description=str(row["description"]),
            requested_by=str(row["requested_by"]),
            risk_level=ApprovalRiskLevel(str(row["risk_level"])),
            source=ApprovalSource(str(row["source"])),
            status=ApprovalStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
            expires_at=(
                coerce_row_timestamp(row["expires_at"])
                if row["expires_at"] is not None
                else None
            ),
            decided_at=(
                coerce_row_timestamp(row["decided_at"])
                if row["decided_at"] is not None
                else None
            ),
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            decision_reason=(
                str(row["decision_reason"])
                if row["decision_reason"] is not None
                else None
            ),
            task_id=(str(row["task_id"]) if row["task_id"] is not None else None),
            consumed_at=(
                coerce_row_timestamp(row["consumed_at"])
                if row["consumed_at"] is not None
                else None
            ),
            evidence_package=(
                EvidencePackage.model_validate_json(str(row["evidence_package"]))
                if row["evidence_package"] is not None
                else None
            ),
            metadata=metadata_raw,
        )
    except (
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ) as exc:
        try:
            row_id = str(row["id"]) if row else "<unknown>"
        except TypeError, KeyError:
            row_id = "<unknown>"
        msg = f"Failed to parse approval row {row_id!r}"
        logger.warning(
            API_APPROVAL_REPO_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class SQLiteApprovalRepository:
    """SQLite-backed approval item repository.

    Provides CRUD operations for approval items using a shared
    ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
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

    async def save(self, item: ApprovalItem) -> None:
        """Upsert an approval item.

        Args:
            item: The approval item to persist.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        evidence_json = (
            item.evidence_package.model_dump_json()
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
            format_iso_utc(item.created_at),
            format_iso_utc(item.expires_at) if item.expires_at else None,
            format_iso_utc(item.decided_at) if item.decided_at else None,
            item.decided_by,
            item.decision_reason,
            item.task_id,
            evidence_json,
            json.dumps(item.metadata),
            format_iso_utc(item.consumed_at) if item.consumed_at else None,
        )
        async with self._write_context():
            try:
                await self._db.execute(_APPROVALS_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(self._db, operation="save", approval_id=item.id)
                msg = f"Constraint violation saving approval {item.id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=item.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(
                    msg,
                    constraint=str(exc),
                ) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="save", approval_id=item.id)
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

        Empty input is a no-op.  Single-item input falls back to the
        scalar ``save()`` path so the per-item error context still
        names the offending id on constraint violation.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        if not items:
            return
        if len(items) == 1:
            await self.save(items[0])
            return
        param_rows = []
        for item in items:
            evidence_json = (
                item.evidence_package.model_dump_json()
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
                    format_iso_utc(item.created_at),
                    format_iso_utc(item.expires_at) if item.expires_at else None,
                    format_iso_utc(item.decided_at) if item.decided_at else None,
                    item.decided_by,
                    item.decision_reason,
                    item.task_id,
                    evidence_json,
                    json.dumps(item.metadata),
                    format_iso_utc(item.consumed_at) if item.consumed_at else None,
                ),
            )
        async with self._write_context():
            try:
                await self._db.executemany(_APPROVALS_UPSERT_SQL, param_rows)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db, operation="save_many", batch_size=len(items)
                )
                msg = f"Constraint violation saving approval batch (size={len(items)})"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    batch_size=len(items),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="save_many", batch_size=len(items)
                )
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

        Uses ``UPDATE ... WHERE id IN (?,...) AND status='pending'
        RETURNING id`` (SQLite >= 3.35) so the compare-and-set is
        atomic at the row level and the returned ids reflect what
        actually transitioned.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        if not ids:
            return ()
        placeholders = ",".join(["?"] * len(ids))
        # ``placeholders`` interpolates only fixed ``?,?,?`` markers -- enum
        # values and ids are bound through ``params`` below.
        sql = (
            "UPDATE approvals SET status = ? "  # noqa: S608
            f"WHERE id IN ({placeholders}) "
            "AND status = ? "
            "RETURNING id"
        )
        params = (ApprovalStatus.EXPIRED.value, *ids, ApprovalStatus.PENDING.value)
        async with self._write_context():
            try:
                async with self._db.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                # Log the rollback failure separately rather than
                # suppressing it -- a silent rollback failure leaves
                # the shared aiosqlite.Connection in an unknown state
                # and the only diagnostic of why subsequent writes
                # may start failing is then lost. Original ``exc`` is
                # still chained on the QueryError so the caller sees
                # the root cause.
                try:
                    await self._db.rollback()
                except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
                    # ``logger.error`` (not ``logger.exception``):
                    # the rollback failure is a structured event, not
                    # a stack-trace dump. ``rollback_exc`` is captured
                    # in ``error_type`` + ``error`` already.
                    log_exception_redacted(
                        logger,
                        API_APPROVAL_REPO_FAILED,
                        rollback_exc,
                        batch_size=len(ids),
                        phase="rollback",
                    )
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
        """Get an approval item by ID.

        Args:
            approval_id: The approval identifier.

        Returns:
            The approval item, or None if not found.

        Raises:
            QueryError: If the database query fails.
        """
        sql = """
            SELECT id, action_type, title, description, requested_by,
                   risk_level, source, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata, consumed_at
            FROM approvals WHERE id = ?
        """
        try:
            cursor = await self._db.execute(sql, (approval_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        """Batch-fetch approval items by id via ``WHERE id IN (...)``.

        Empty input short-circuits to ``()`` without issuing SQL.
        Missing ids are simply absent from the result.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if not ids:
            return ()
        placeholders = ",".join(["?"] * len(ids))
        # ``placeholders`` is a literal "?,?,..." string we generated
        # ourselves from ``len(ids)``; ids themselves are bound as
        # parameters in the ``execute`` call below.
        sql = f"""
            SELECT id, action_type, title, description, requested_by,
                   risk_level, source, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata, consumed_at
            FROM approvals WHERE id IN ({placeholders})
        """  # noqa: S608  -- placeholders is a closed-set "?,?,..." pattern
        try:
            cursor = await self._db.execute(sql, tuple(ids))
            rows = await cursor.fetchall()
            items = tuple(_row_to_item(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = """
            SELECT id, action_type, title, description, requested_by,
                   risk_level, source, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata, consumed_at
            FROM approvals
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(_row_to_item(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.risk_level is not None:
            clauses.append("risk_level = ?")
            params.append(filter_spec.risk_level.value)
        if filter_spec.action_type is not None:
            clauses.append("action_type = ?")
            params.append(filter_spec.action_type)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT id, action_type, title, description, requested_by,
                   risk_level, source, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata, consumed_at
            FROM approvals WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608  -- ``where`` is built from a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(_row_to_item(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.risk_level is not None:
            clauses.append("risk_level = ?")
            params.append(filter_spec.risk_level.value)
        if filter_spec.action_type is not None:
            clauses.append("action_type = ?")
            params.append(filter_spec.action_type)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"""
            SELECT COUNT(*) FROM approvals WHERE {where}
        """  # noqa: S608  -- ``where`` is built from a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
            assert row is not None  # noqa: S101  -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        **updates: object,  # noqa: ARG002
    ) -> bool:
        """Atomic compare-and-set for approval state transitions (ADR-0001 D7).

        Transitions the approval from ``from_state`` to ``to_state`` iff
        the current persisted status matches ``from_state``. Returns ``True``
        iff the state transition succeeded.

        ``**updates`` is ignored for now; future versions may support
        ``expired_at`` and other status-correlated fields.

        Args:
            entity_id: The approval id.
            from_state: Expected current status.
            to_state: Target status.
            **updates: Status-correlated fields (reserved, currently unused).

        Returns:
            ``True`` iff the transition succeeded, ``False`` on state
            mismatch or when no row exists.

        Raises:
            QueryError: On database errors.
        """
        sql = "UPDATE approvals SET status = ? WHERE id = ? AND status = ?"
        params = (to_state.value, entity_id, from_state.value)
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    operation="transition_if",
                    approval_id=entity_id,
                )
                msg = f"Failed to transition approval {entity_id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

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
            "UPDATE approvals SET consumed_at = ? "
            "WHERE id = ? AND status = ? AND consumed_at IS NULL"
        )
        params = (
            format_iso_utc(consumed_at),
            approval_id,
            ApprovalStatus.APPROVED.value,
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    operation="consume_if_approved",
                    approval_id=approval_id,
                )
                msg = f"Failed to consume approval {approval_id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=approval_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Delete an approval item by ID.

        Args:
            approval_id: The approval identifier.

        Returns:
            True if the item was deleted, False if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        sql = "DELETE FROM approvals WHERE id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (approval_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="delete", approval_id=approval_id
                )
                msg = f"Failed to delete approval {approval_id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=approval_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0
