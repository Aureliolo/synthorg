"""Postgres implementation of the SsrfViolationRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/ssrf_violation_repo.py.
Postgres stores timestamps as native TIMESTAMPTZ and port as BIGINT.
"""

from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import dict_row
from pydantic import AwareDatetime, ValidationError

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
    PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
)
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.errors import DuplicateRecordError, QueryError
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLS = (
    "id, timestamp, url, hostname, port, resolved_ip, "
    "blocked_range, provider_name, status, resolved_by, resolved_at"
)


class PostgresSsrfViolationRepository:
    """Postgres implementation of the SsrfViolationRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, violation: SsrfViolation) -> None:
        """Persist a new SSRF violation.

        Args:
            violation: The violation to save.

        Raises:
            DuplicateRecordError: If a violation with the same ID exists.
            QueryError: If the save fails.
        """
        ts_utc = normalize_utc(violation.timestamp)
        resolved_at_utc = (
            normalize_utc(violation.resolved_at) if violation.resolved_at else None
        )

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"INSERT INTO ssrf_violations ({_COLS}) "  # noqa: S608
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"SSRF violation {violation.id!r} already exists"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                violation_id=violation.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save SSRF violation {violation.id!r}"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                violation_id=violation.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        violation_id: NotBlankStr,
    ) -> SsrfViolation | None:
        """Retrieve a violation by ID."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLS} FROM ssrf_violations WHERE id = %s",  # noqa: S608
                    (violation_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to get SSRF violation {violation_id!r}"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                violation_id=violation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
            raise QueryError(msg) from exc

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = 100,
    ) -> tuple[SsrfViolation, ...]:
        """List violations, optionally filtered by status."""
        if limit <= 0:
            msg = "limit must be positive"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                error=msg,
                limit=limit,
            )
            raise ValueError(msg)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                if status is not None:
                    await cur.execute(
                        f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                        "WHERE status = %s ORDER BY timestamp DESC LIMIT %s",
                        (status.value, limit),
                    )
                else:
                    await cur.execute(
                        f"SELECT {_COLS} FROM ssrf_violations "  # noqa: S608
                        "ORDER BY timestamp DESC LIMIT %s",
                        (limit,),
                    )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list SSRF violations"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                status=status.value if status is not None else None,
                limit=limit,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[SsrfViolation] = []
        for row in rows:
            try:
                results.append(_row_to_violation(row))
            except (ValueError, ValidationError) as exc:
                # Do not silently drop malformed violation rows:
                # list_violations is how operators audit the full
                # history of blocked SSRF attempts, so returning a
                # partial list would hide security-relevant events.
                row_id = row.get("id") if row else "unknown"
                logger.warning(
                    PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED,
                    row_id=row_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Failed to deserialize SSRF violation row {row_id!r}"
                raise QueryError(msg) from exc
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
            msg = "Cannot transition a violation back to PENDING"
            raise ValueError(msg)

        resolved_at_utc = normalize_utc(resolved_at)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ssrf_violations "
                    "SET status = %s, resolved_by = %s, resolved_at = %s "
                    "WHERE id = %s AND status = 'pending'",
                    (
                        status.value,
                        resolved_by,
                        resolved_at_utc,
                        violation_id,
                    ),
                )
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to update SSRF violation {violation_id!r} status"
            logger.warning(
                PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED,
                violation_id=violation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return updated


def _row_to_violation(row: dict[str, object]) -> SsrfViolation:
    """Convert a Postgres row to an SsrfViolation."""
    return SsrfViolation(
        id=str(row["id"]),
        timestamp=normalize_utc(cast("datetime", row["timestamp"])),
        url=str(row["url"]),
        hostname=str(row["hostname"]),
        port=int(cast("int", row["port"])),
        resolved_ip=cast("str | None", row.get("resolved_ip")),
        blocked_range=cast("str | None", row.get("blocked_range")),
        provider_name=cast("str | None", row.get("provider_name")),
        status=SsrfViolationStatus(str(row["status"])),
        resolved_by=cast("str | None", row.get("resolved_by")),
        resolved_at=(
            normalize_utc(cast("datetime", row["resolved_at"]))
            if row.get("resolved_at")
            else None
        ),
    )
