"""SQLite repository implementation for SSRF violation records."""

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Any

import aiosqlite
from pydantic import AwareDatetime, ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
    PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.errors import DuplicateRecordError, PersistenceError
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_COLS = (
    "id, timestamp, url, hostname, port, resolved_ip, "
    "blocked_range, provider_name, status, resolved_by, resolved_at"
)


def _is_unique_constraint_error(exc: sqlite3.IntegrityError) -> bool:
    """Return True for UNIQUE/PRIMARY KEY violations."""
    return exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
    }


class SQLiteSsrfViolationRepository:
    """SQLite implementation of the SsrfViolationRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_lock: Optional shared lock protecting multi-statement
            transactions.  Defaults to a per-instance lock for test
            ergonomics.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing errors."""
        try:
            await self._db.rollback()
        except Exception:
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                error="rollback failed",
                exc_info=True,
            )

    async def save(self, violation: SsrfViolation) -> None:
        """Persist a new SSRF violation.

        Args:
            violation: The violation to save.

        Raises:
            DuplicateRecordError: If a violation with the same ID exists.
            PersistenceError: If the save fails.
        """
        ts_utc = format_iso_utc(violation.timestamp)
        resolved_at_utc = (
            format_iso_utc(violation.resolved_at) if violation.resolved_at else None
        )

        try:
            async with self._write_lock:
                await self._db.execute(
                    f"INSERT INTO ssrf_violations ({_COLS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        violation.id,
                        ts_utc,
                        violation.url,
                        violation.hostname,
                        violation.port,
                        violation.resolved_ip,
                        violation.blocked_range,
                        violation.provider_name,
                        violation.status.value,
                        violation.resolved_by,
                        resolved_at_utc,
                    ),
                )
                await self._db.commit()
        except sqlite3.IntegrityError as exc:
            await self._rollback_quietly()
            if _is_unique_constraint_error(exc):
                msg = f"SSRF violation {violation.id!r} already exists"
                raise DuplicateRecordError(msg) from exc
            msg = "Failed to save SSRF violation"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = "Failed to save SSRF violation"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

    async def get(
        self,
        violation_id: NotBlankStr,
    ) -> SsrfViolation | None:
        """Retrieve a violation by ID."""
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                "WHERE id = ?",
                (violation_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to get SSRF violation"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

        if row is None:
            return None
        try:
            return _row_to_violation(row)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize SSRF violation {violation_id!r}"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                violation_id=violation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = 100,
    ) -> tuple[SsrfViolation, ...]:
        """List violations, optionally filtered by status."""
        if limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)

        if status is not None:
            query = (
                f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                "WHERE status = ? ORDER BY timestamp DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (status.value, limit)
        else:
            query = (
                f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                "ORDER BY timestamp DESC LIMIT ?"
            )
            params = (limit,)

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list SSRF violations"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

        results: list[SsrfViolation] = []
        for row in rows:
            try:
                results.append(_row_to_violation(row))
            except (ValueError, ValidationError) as exc:
                # Surface corrupted audit rows as a hard failure.
                # Silently skipping would hide security-relevant
                # events from operators auditing SSRF block history
                # and leave the Postgres sibling and SQLite repo
                # with divergent contracts.
                row_id = row[0] if row else "unknown"
                msg = f"Failed to deserialize SSRF violation row {row_id!r}: {exc}"
                logger.warning(
                    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                    row_id=row_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
        return tuple(results)

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: SsrfViolationStatus,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        """Update a violation's status (allow or deny).

        Rejects transitions back to PENDING.

        Raises:
            ValueError: If status is PENDING.
        """
        if status == SsrfViolationStatus.PENDING:
            msg = (
                f"Cannot transition violation {violation_id!r} "
                f"to PENDING (target status must be ALLOW or DENY)"
            )
            # Log the rejection at WARNING with full context so an
            # operator investigating an audit-trail anomaly can see
            # who attempted the bad transition.
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                violation_id=violation_id,
                attempted_status=status.value,
                resolved_by=resolved_by,
                error=msg,
            )
            raise ValueError(msg)

        resolved_at_utc = format_iso_utc(resolved_at)
        try:
            async with self._write_lock:
                cursor = await self._db.execute(
                    "UPDATE ssrf_violations "
                    "SET status = ?, resolved_by = ?, resolved_at = ? "
                    "WHERE id = ? AND status = 'pending'",
                    (
                        status.value,
                        resolved_by,
                        resolved_at_utc,
                        violation_id,
                    ),
                )
                await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = "Failed to update SSRF violation status"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

        return cursor.rowcount > 0


def _row_to_violation(row: Any) -> SsrfViolation:
    """Convert a SQLite row to an SsrfViolation."""
    (
        id_,
        timestamp,
        url,
        hostname,
        port,
        resolved_ip,
        blocked_range,
        provider_name,
        status,
        resolved_by,
        resolved_at,
    ) = row

    return SsrfViolation(
        id=id_,
        timestamp=parse_iso_utc(timestamp),
        url=url,
        hostname=hostname,
        port=port,
        resolved_ip=resolved_ip or None,
        blocked_range=blocked_range or None,
        provider_name=provider_name,
        status=SsrfViolationStatus(status),
        resolved_by=resolved_by,
        resolved_at=(parse_iso_utc(resolved_at) if resolved_at else None),
    )
