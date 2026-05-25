"""SQLite repository for worker claim dedup persistence.

Backs :class:`SeenClaimsRepository` against an aiosqlite connection.
``mark_seen`` uses ``INSERT ... ON CONFLICT DO NOTHING`` so the first
writer inserts and concurrent writers observe a no-op insert;
``cursor.rowcount`` distinguishes first-write from duplicate without a
separate read. ``is_completed`` is the pre-execute existence check
workers consult before re-running a claim.
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
    PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED,
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

    async def is_completed(
        self,
        *,
        idempotency_key: NotBlankStr,
    ) -> bool:
        """Return ``True`` when a row for ``idempotency_key`` exists.

        Returns:
            ``True`` when a row for ``idempotency_key`` exists, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT 1 FROM seen_claims WHERE idempotency_key = ? LIMIT 1",
                (str(idempotency_key),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to look up seen claim {idempotency_key!r}"
            logger.warning(
                PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED,
                idempotency_key=str(idempotency_key),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return row is not None

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: AwareDatetime,
        ttl_seconds: float,
    ) -> bool:
        """Insert the dedup row; return ``True`` only on first write.

        The backend ``write_context`` is intentionally held across the
        COMMIT so concurrent ``mark_seen`` callers serialise on the
        same SQLite connection. Under WAL mode SQLite only serialises
        writers (not readers) at the file lock, but the in-process
        lock keeps ``cursor.rowcount`` semantics clean: a duplicate
        observed by the conflict clause returns ``rowcount == 0`` only
        if the first writer's transaction has already been committed
        when the second one queries. Releasing the lock pre-commit
        would let a sibling caller observe a stale ``rowcount`` and
        treat a duplicate as a first-write.

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
        """Delete rows past their ``expires_at`` boundary.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
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
