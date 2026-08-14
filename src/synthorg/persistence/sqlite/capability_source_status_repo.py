# module-kind: repository
"""SQLite repository for per-source capability-ingest status.

One upserted row per registered source. A failed attempt writes a row like
any other: the record of a source going quiet is exactly what this table
exists to keep, so a failure here would hide the very fact it is meant to
report.
"""

import sqlite3
from typing import Final

import aiosqlite
from aiosqlite import Row

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_SOURCE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.providers.capability_sources.status import CapabilitySourceStatus

logger = get_logger(__name__)

_SELECT_COLS: Final[str] = (
    "source_label, last_attempted_at, last_succeeded_at, last_error, "
    "rows_read, rows_skipped, scores_written, feed_url"
)

_UPSERT_SQL = f"""
    INSERT INTO capability_source_statuses ({_SELECT_COLS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_label) DO UPDATE SET
        last_attempted_at = excluded.last_attempted_at,
        last_succeeded_at = excluded.last_succeeded_at,
        last_error = excluded.last_error,
        rows_read = excluded.rows_read,
        rows_skipped = excluded.rows_skipped,
        scores_written = excluded.scores_written,
        feed_url = excluded.feed_url
"""  # noqa: S608 -- column list is a compile-time constant


def _params(
    entity: CapabilitySourceStatus,
) -> tuple[str, str | None, str | None, str, int, int, int, str]:
    """Return the positional bind parameters for one status row.

    Returns:
        The eight column values in ``_SELECT_COLS`` order.
    """
    return (
        str(entity.source_label),
        format_iso_utc(entity.last_attempted_at) if entity.last_attempted_at else None,
        format_iso_utc(entity.last_succeeded_at) if entity.last_succeeded_at else None,
        entity.last_error,
        entity.rows_read,
        entity.rows_skipped,
        entity.scores_written,
        entity.feed_url,
    )


def _row_to_status(row: Row) -> CapabilitySourceStatus:
    """Convert a database row into a :class:`CapabilitySourceStatus`.

    Returns:
        The parsed status.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        attempted = row["last_attempted_at"]
        succeeded = row["last_succeeded_at"]
        return CapabilitySourceStatus(
            source_label=NotBlankStr(str(row["source_label"])),
            last_attempted_at=coerce_row_timestamp(attempted) if attempted else None,
            last_succeeded_at=coerce_row_timestamp(succeeded) if succeeded else None,
            last_error=str(row["last_error"]),
            rows_read=int(row["rows_read"]),
            rows_skipped=int(row["rows_skipped"]),
            scores_written=int(row["scores_written"]),
            feed_url=str(row["feed_url"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        error_type = type(exc).__name__
        error_desc = safe_error_description(exc)
        msg = f"Failed to parse capability source status row: {error_type}"
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            operation="deserialize",
            error_type=error_type,
            error=error_desc,
        )
        raise QueryError(msg) from exc


class SQLiteCapabilitySourceStatusRepository:
    """SQLite-backed capability-source status repository.

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

    async def save(self, entity: CapabilitySourceStatus) -> None:
        """Upsert one source's status by label.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        label = str(entity.source_label)
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, _params(entity))
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._rollback(label)
                msg = (
                    f"Constraint violation saving capability source status "
                    f"for {label!r}: {safe_error_description(exc)}"
                )
                self._log_failure("save", exc, source_label=label)
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback(label)
                msg = (
                    f"Failed to save capability source status for {label!r}: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                self._log_failure("save", exc, source_label=label)
                raise QueryError(msg) from exc

    def _log_failure(
        self,
        operation: str,
        exc: Exception,
        *,
        source_label: str | None = None,
    ) -> None:
        """Emit the repository-level diagnostic for a failed operation.

        The typed error travels to the caller; this is what an operator
        reading the log sees, and every sibling repository emits it, so a
        database fault here would otherwise be the one that surfaced
        without operational context.
        """
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            operation=operation,
            source_label=source_label,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    async def _rollback(self, label: str) -> None:
        """Roll back the current transaction, logging any rollback failure."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            log_exception_redacted(
                logger,
                PROVIDER_CAPABILITY_SOURCE_FAILED,
                exc,
                phase="rollback",
                source_label=label,
            )

    async def get(self, entity_id: NotBlankStr) -> CapabilitySourceStatus | None:
        """Get one source's status, or ``None`` when never attempted.

        Returns:
            The matching status, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM capability_source_statuses "  # noqa: S608
            "WHERE source_label = ?"
        )
        try:
            async with self._db.execute(sql, (str(entity_id),)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = (
                f"Failed to fetch capability source status {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            self._log_failure("get", exc, source_label=str(entity_id))
            raise QueryError(msg) from exc
        return None if row is None else _row_to_status(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CapabilitySourceStatus, ...]:
        """List statuses ordered by source label ascending (paginated).

        Returns:
            The matching statuses.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PROVIDER_CAPABILITY_SOURCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM capability_source_statuses "  # noqa: S608
            "ORDER BY source_label ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_status(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list capability source statuses"
            self._log_failure("list_items", exc)
            raise QueryError(msg) from exc

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete one source's status.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM capability_source_statuses WHERE source_label = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (str(entity_id),)) as cursor:
                    rowcount = cursor.rowcount
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback(str(entity_id))
                msg = (
                    f"Failed to delete capability source status {entity_id!r}: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                self._log_failure("delete", exc, source_label=str(entity_id))
                raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["SQLiteCapabilitySourceStatusRepository"]
