"""SQLite repository for worker claim dedup persistence.

Backs :class:`SeenClaimsRepository` against an aiosqlite connection.
Uses an ``INSERT ... ON CONFLICT DO NOTHING`` so the first writer
inserts and concurrent writers observe a no-op insert. Returning
``cursor.rowcount`` distinguishes first-write from duplicate without
a separate read.
"""

import contextlib
import sqlite3
from datetime import datetime, timedelta

import aiosqlite
from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SEEN_CLAIMS_MARK_FAILED,
    PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED,
    PERSISTENCE_SEEN_CLAIMS_PRUNED,
)
from synthorg.persistence._shared import format_iso_utc, normalize_utc
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


class SQLiteSeenClaimsRepository:
    """SQLite implementation of :class:`SeenClaimsRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        """Bind to *db* and serialise writes via the backend *write_context*."""
        self._db = db
        self._write_context = write_context

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: AwareDatetime,
        ttl_seconds: float,
    ) -> bool:
        """Insert the dedup row; return ``True`` only on first write."""
        seen_at = normalize_utc(now)
        expires_at: datetime = seen_at + timedelta(seconds=ttl_seconds)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    """
                    INSERT INTO seen_claims (
                        idempotency_key, claim_id, seen_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO NOTHING
                    """,
                    (
                        str(idempotency_key),
                        str(claim_id),
                        format_iso_utc(seen_at),
                        format_iso_utc(expires_at),
                    ),
                )
                inserted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to mark claim {idempotency_key!r} as seen"
                logger.warning(
                    PERSISTENCE_SEEN_CLAIMS_MARK_FAILED,
                    idempotency_key=str(idempotency_key),
                    claim_id=str(claim_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return inserted

    async def prune_expired(self, now: AwareDatetime) -> int:
        """Delete rows past their ``expires_at`` boundary."""
        cutoff_iso = format_iso_utc(normalize_utc(now))
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM seen_claims WHERE expires_at < ?",
                    (cutoff_iso,),
                )
                removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to prune expired seen_claims rows"
                logger.warning(
                    PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_SEEN_CLAIMS_PRUNED,
                removed=removed,
            )
        return removed
