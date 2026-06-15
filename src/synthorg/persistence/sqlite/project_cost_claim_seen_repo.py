"""SQLite repository for durable project-cost-claim dedup.

Backs :class:`ProjectCostClaimSeenRepository` against an aiosqlite
connection. ``mark_seen`` uses ``INSERT ... ON CONFLICT DO NOTHING`` so
the first writer inserts and concurrent writers observe a no-op insert;
``cursor.rowcount`` distinguishes first-write from duplicate without a
separate read. ``has_seen`` is the pre-increment existence check
``CostTracker`` consults so a post-restart redelivery cannot re-bill.
"""

import contextlib
import sqlite3
from datetime import datetime, timedelta

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.project_cost_claim_seen import (
    PERSISTENCE_COST_CLAIM_SEEN_LOOKUP_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_MARK_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_PRUNE_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_PRUNED,
)
from synthorg.persistence._shared import format_iso_utc, normalize_utc
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class SQLiteProjectCostClaimSeenRepository:
    """SQLite implementation of :class:`ProjectCostClaimSeenRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        """Bind to *db* and serialise writes via the backend *write_context*."""
        self._db = db
        self._write_context = write_context

    async def has_seen(
        self,
        *,
        claim_id: NotBlankStr,
    ) -> bool:
        """Return ``True`` when a row for ``claim_id`` exists.

        Returns:
            ``True`` when a row for ``claim_id`` exists, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT 1 FROM project_cost_claim_seen WHERE claim_id = ? LIMIT 1",
                (str(claim_id),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to look up cost claim {claim_id!r}"
            logger.warning(
                PERSISTENCE_COST_CLAIM_SEEN_LOOKUP_FAILED,
                claim_id=str(claim_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return row is not None

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        """Insert the dedup row; return ``True`` only on first write.

        The backend ``write_context`` is held across the COMMIT so
        concurrent ``mark_seen`` callers serialise on the same SQLite
        connection and observe clean ``cursor.rowcount`` semantics
        (a duplicate sees ``rowcount == 0`` only after the first
        writer's transaction has committed).

        Returns:
            ``True`` when this call inserted the dedup row, ``False`` when a previous
            call had already inserted it.

        Raises:
            QueryError: If the database query fails.
        """
        seen_at = normalize_utc(now)
        expires_at: datetime = seen_at + timedelta(seconds=ttl_seconds)
        async with self._write_context():
            try:
                async with self._db.execute(
                    """
                    INSERT INTO project_cost_claim_seen (
                        claim_id, project_id, seen_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(claim_id) DO NOTHING
                    """,
                    (
                        str(claim_id),
                        str(project_id),
                        format_iso_utc(seen_at),
                        format_iso_utc(expires_at),
                    ),
                ) as cursor:
                    inserted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to mark cost claim {claim_id!r} as seen"
                logger.warning(
                    PERSISTENCE_COST_CLAIM_SEEN_MARK_FAILED,
                    claim_id=str(claim_id),
                    project_id=str(project_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return inserted

    async def prune_expired(self, now: datetime) -> int:
        """Delete rows past their ``expires_at`` boundary.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        cutoff_iso = format_iso_utc(normalize_utc(now))
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM project_cost_claim_seen WHERE expires_at < ?",
                    (cutoff_iso,),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to prune expired project_cost_claim_seen rows"
                logger.warning(
                    PERSISTENCE_COST_CLAIM_SEEN_PRUNE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_COST_CLAIM_SEEN_PRUNED,
                removed=removed,
            )
        return removed
