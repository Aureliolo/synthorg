"""SQLite repository implementation for approval items."""

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from aiosqlite import Row
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.evidence import EvidencePackage
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_FAILED,
    API_APPROVAL_REPO_FETCHED,
    API_APPROVAL_REPO_LISTED,
)
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_APPROVALS_UPSERT_SQL = """
    INSERT INTO approvals (
        id, action_type, title, description, requested_by,
        risk_level, status, created_at, expires_at,
        decided_at, decided_by, decision_reason,
        task_id, evidence_package, metadata
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        action_type = excluded.action_type,
        title = excluded.title,
        description = excluded.description,
        requested_by = excluded.requested_by,
        risk_level = excluded.risk_level,
        status = excluded.status,
        expires_at = excluded.expires_at,
        decided_at = excluded.decided_at,
        decided_by = excluded.decided_by,
        decision_reason = excluded.decision_reason,
        task_id = excluded.task_id,
        evidence_package = excluded.evidence_package,
        metadata = excluded.metadata
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
    except MemoryError, RecursionError:
        raise
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        logger.error(
            API_APPROVAL_REPO_FAILED,
            phase="rollback",
            operation=operation,
            error_type=type(rollback_exc).__name__,
            error=safe_error_description(rollback_exc),
            **log_context,
        )


def _row_to_item(row: Row) -> ApprovalItem:
    """Convert a database row to an ApprovalItem.

    Args:
        row: A row from aiosqlite with ``row_factory = aiosqlite.Row``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
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
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

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
            item.status.value,
            format_iso_utc(item.created_at),
            format_iso_utc(item.expires_at) if item.expires_at else None,
            format_iso_utc(item.decided_at) if item.decided_at else None,
            item.decided_by,
            item.decision_reason,
            item.task_id,
            evidence_json,
            json.dumps(item.metadata),
        )
        async with self._write_lock:
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
                    item.status.value,
                    format_iso_utc(item.created_at),
                    format_iso_utc(item.expires_at) if item.expires_at else None,
                    format_iso_utc(item.decided_at) if item.decided_at else None,
                    item.decided_by,
                    item.decision_reason,
                    item.task_id,
                    evidence_json,
                    json.dumps(item.metadata),
                ),
            )
        async with self._write_lock:
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
        """
        if not ids:
            return ()
        placeholders = ",".join(["?"] * len(ids))
        sql = (
            f"UPDATE approvals SET status = '{ApprovalStatus.EXPIRED.value}' "  # noqa: S608
            f"WHERE id IN ({placeholders}) "
            f"AND status = '{ApprovalStatus.PENDING.value}' "
            "RETURNING id"
        )
        async with self._write_lock:
            try:
                async with self._db.execute(sql, tuple(ids)) as cursor:
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
                    logger.error(
                        API_APPROVAL_REPO_FAILED,
                        batch_size=len(ids),
                        phase="rollback",
                        error_type=type(rollback_exc).__name__,
                        error=safe_error_description(rollback_exc),
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
                   risk_level, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata
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
        """
        if not ids:
            return ()
        placeholders = ",".join(["?"] * len(ids))
        # ``placeholders`` is a literal "?,?,..." string we generated
        # ourselves from ``len(ids)``; ids themselves are bound as
        # parameters in the ``execute`` call below.
        sql = f"""
            SELECT id, action_type, title, description, requested_by,
                   risk_level, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata
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
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items with optional filters (paginated, newest-first).

        ``ORDER BY created_at DESC, id DESC`` keeps cursor pagination
        stable when two approvals share a ``created_at`` timestamp;
        the ``id`` tiebreaker prevents duplicates / gaps under
        concurrent inserts.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error=msg,
                param="limit",
                value=limit,
            )
            raise QueryError(msg)
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error=msg,
                param="offset",
                value=offset,
            )
            raise QueryError(msg)
        # Clamp limit at ``_MAX_PAGE_LIMIT`` so a runaway caller cannot
        # exhaust memory with a single oversized fetch.
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if risk_level is not None:
            clauses.append("risk_level = ?")
            params.append(risk_level.value)
        if action_type is not None:
            clauses.append("action_type = ?")
            params.append(action_type)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT id, action_type, title, description, requested_by,
                   risk_level, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata
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
            msg = "Failed to list approvals"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(API_APPROVAL_REPO_LISTED, count=len(items))
        return items

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
        async with self._write_lock:
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
