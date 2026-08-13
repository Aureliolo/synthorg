# module-kind: repository
"""Postgres repository for per-source capability-ingest status.

Sibling of :class:`SQLiteCapabilitySourceStatusRepository` backed by
``psycopg_pool.AsyncConnectionPool``. One upserted row per registered
source; a failed attempt writes a row like any other, because the record
of a source going quiet is exactly what this table exists to keep.
"""

from typing import Final

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_SOURCE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus

logger = get_logger(__name__)

_SELECT_COLS: Final[str] = (
    "source_label, last_attempted_at, last_succeeded_at, last_error, "
    "rows_read, rows_skipped, scores_written, feed_url"
)

_UPSERT_SQL = f"""
    INSERT INTO capability_source_statuses ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source_label) DO UPDATE SET
        last_attempted_at = EXCLUDED.last_attempted_at,
        last_succeeded_at = EXCLUDED.last_succeeded_at,
        last_error = EXCLUDED.last_error,
        rows_read = EXCLUDED.rows_read,
        rows_skipped = EXCLUDED.rows_skipped,
        scores_written = EXCLUDED.scores_written,
        feed_url = EXCLUDED.feed_url
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


def _row_to_status(row: DictRow) -> CapabilitySourceStatus:
    """Convert a Postgres dict row into a :class:`CapabilitySourceStatus`.

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
    except (ValueError, TypeError, KeyError) as exc:
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


class PostgresCapabilitySourceStatusRepository:
    """Postgres-backed capability-source status repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: CapabilitySourceStatus) -> None:
        """Upsert one source's status by label.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        label = str(entity.source_label)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, _params(entity))
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving capability source status for "
                f"{label!r}: {safe_error_description(exc)}"
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save capability source status for {label!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> CapabilitySourceStatus | None:
        """Get one source's status, or ``None`` when never attempted.

        Returns:
            The matching status, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM capability_source_statuses "  # noqa: S608
            "WHERE source_label = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (str(entity_id),))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = (
                f"Failed to fetch capability source status {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
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
            "ORDER BY source_label ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_status(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list capability source statuses"
            logger.warning(
                PROVIDER_CAPABILITY_SOURCE_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete one source's status.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM capability_source_statuses WHERE source_label = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (str(entity_id),))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to delete capability source status {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresCapabilitySourceStatusRepository"]
