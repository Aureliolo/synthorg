"""SQLite repository implementation for SSRF violation records."""

import contextlib
import sqlite3
from datetime import datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.ssrf_violation import (
    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
    PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus

logger = get_logger(__name__)

_COLS = (
    "id, timestamp, url, hostname, port, resolved_ip, "
    "blocked_range, provider_name, status, resolved_by, resolved_at"
)


class SQLiteSsrfViolationRepository:
    """SQLite implementation of the SsrfViolationRepository protocol.

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
        self._write_context = write_context

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing driver errors.

        Any rollback failure is logged at WARNING and swallowed so the
        caller's outer exception remains the operative one. Narrowed to
        the driver-error surface, so ``MemoryError`` / ``RecursionError``
        propagate naturally.
        """
        try:
            await self._db.rollback()
        # aiosqlite raises a bare ValueError("Connection closed") for a closed
        # connection; treat it as a driver-level rollback failure so this
        # best-effort rollback never masks the caller's primary error.
        except (sqlite3.Error, ValueError) as exc:
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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

        async with self._write_context():
            try:
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
                if is_unique_constraint_error(exc):
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
        """Retrieve a violation by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
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
        except (ValueError, ValidationError, TypeError) as exc:
            msg = f"Failed to deserialize SSRF violation {violation_id!r}"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                violation_id=violation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        """List violations ordered by id ascending (generic IdKeyed surface).

        Returns:
            The matching entities.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED
        )
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                "ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
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
            except (ValueError, ValidationError, TypeError) as exc:
                msg = "Failed to deserialize SSRF violation row"
                logger.warning(
                    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
        return tuple(results)

    async def delete(self, violation_id: NotBlankStr) -> bool:
        """Delete a violation by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM ssrf_violations WHERE id = ?",
                    (violation_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete SSRF violation {violation_id!r}"
                logger.warning(
                    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                    violation_id=violation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
        return deleted

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[SsrfViolation, ...]:
        """List violations, optionally filtered by status.

        Returns:
            The matching entities.

        Raises:
            ValueError: If an argument fails validation.
            PersistenceError: If the persistence layer rejects the operation.
        """
        if limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)

        if status is not None:
            query = (
                f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                "WHERE status = ? ORDER BY timestamp DESC LIMIT ?"
            )
            params: tuple[object, ...] = (status.value, limit)
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
            except (ValueError, ValidationError, TypeError) as exc:
                # Surface corrupted audit rows as a hard failure.
                # Silently skipping would hide security-relevant
                # events from operators auditing SSRF block history
                # and leave the Postgres sibling and SQLite repo
                # with divergent contracts.
                row_id = row[0] if row else "unknown"
                msg = f"Failed to deserialize SSRF violation row {row_id!r}: {safe_error_description(exc)}"  # noqa: E501
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
        resolved_at: datetime,
    ) -> bool:
        """Update a violation's status (allow or deny).

        Rejects transitions back to PENDING.

        Raises:
            ValueError: If status is PENDING.
            PersistenceError: If the persistence layer rejects the operation.

        Returns:
            True when the operation succeeded, False otherwise.
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
        async with self._write_context():
            try:
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


def _row_to_violation(row: aiosqlite.Row) -> SsrfViolation:
    """Convert a SQLite row to an SsrfViolation.

    Returns:
        Result of type ``SsrfViolation``.
    """
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
        timestamp=coerce_row_timestamp(timestamp),
        url=url,
        hostname=hostname,
        port=port,
        resolved_ip=resolved_ip or None,
        blocked_range=blocked_range or None,
        provider_name=provider_name,
        status=SsrfViolationStatus(status),
        resolved_by=resolved_by,
        # Distinguish SQL NULL from empty string: only ``None``
        # represents an unresolved violation; an empty string is
        # corrupt data and must surface via the strict marshaller.
        resolved_at=(
            coerce_row_timestamp(resolved_at) if resolved_at is not None else None
        ),
    )
