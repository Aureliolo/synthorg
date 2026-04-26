"""SQLite repository implementation for approval items."""

import asyncio
import json
import sqlite3

import aiosqlite
from aiosqlite import Row
from pydantic import ValidationError

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.evidence import EvidencePackage
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_FAILED,
    API_APPROVAL_REPO_FETCHED,
    API_APPROVAL_REPO_LISTED,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.errors import ConstraintViolationError, QueryError

logger = get_logger(__name__)

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
            created_at=parse_iso_utc(str(row["created_at"])),
            expires_at=(
                parse_iso_utc(str(row["expires_at"]))
                if row["expires_at"] is not None
                else None
            ),
            decided_at=(
                parse_iso_utc(str(row["decided_at"]))
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
                await self._db.rollback()
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
                await self._db.rollback()
                msg = f"Failed to save approval {item.id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=item.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

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

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items with optional filters.

        Args:
            status: Filter by approval status.
            risk_level: Filter by risk level.
            action_type: Filter by action type.

        Returns:
            Tuple of matching approval items.
        """
        clauses: list[str] = []
        params: list[str] = []
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
        sql = f"""
            SELECT id, action_type, title, description, requested_by,
                   risk_level, status, created_at, expires_at,
                   decided_at, decided_by, decision_reason,
                   task_id, evidence_package, metadata
            FROM approvals WHERE {where}
            ORDER BY created_at DESC
        """  # noqa: S608
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
                await self._db.rollback()
                msg = f"Failed to delete approval {approval_id!r}"
                logger.warning(
                    API_APPROVAL_REPO_FAILED,
                    approval_id=approval_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0
