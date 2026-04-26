"""SQLite-backed idempotency-key repository (#1599).

Atomic claim semantics rely on ``INSERT OR IGNORE`` followed by an
``UPDATE`` of any pre-existing row that has expired or previously
failed. The shared backend ``write_lock`` serialises writes so the
discriminator returned to the caller never races against a concurrent
claim of the same ``(scope, key)``.
"""

import asyncio
import contextlib
import sqlite3
from datetime import datetime

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.idempotency import (
    IDEMPOTENCY_PERSISTENCE_ERROR,
)
from synthorg.persistence.errors import QueryError
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
)

logger = get_logger(__name__)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteIdempotencyRepository:
    """SQLite implementation of :class:`IdempotencyRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: datetime,
    ) -> IdempotencyClaim:
        """Atomically claim ``(scope, key)`` for *ttl_seconds*."""
        from datetime import timedelta  # noqa: PLC0415

        expires_at = now + timedelta(seconds=ttl_seconds)
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "SELECT status, response_body, expires_at "
                    "FROM idempotency_keys WHERE scope = ? AND key = ?",
                    (scope, key),
                )
                row = await cursor.fetchone()

                if row is not None:
                    status = str(row["status"])
                    row_expires = _parse_dt(row["expires_at"])
                    if row_expires > now and status == "completed":
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.COMPLETED,
                            cached_response=row["response_body"],
                        )
                    if row_expires > now and status == "in_flight":
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.IN_FLIGHT,
                        )
                    # Expired OR failed -- overwrite as a fresh claim.
                    await self._db.execute(
                        "UPDATE idempotency_keys "
                        "SET status = 'in_flight', response_hash = NULL, "
                        "response_body = NULL, created_at = ?, expires_at = ? "
                        "WHERE scope = ? AND key = ?",
                        (now.isoformat(), expires_at.isoformat(), scope, key),
                    )
                else:
                    await self._db.execute(
                        "INSERT INTO idempotency_keys "
                        "(scope, key, status, created_at, expires_at) "
                        "VALUES (?, ?, 'in_flight', ?, ?)",
                        (scope, key, now.isoformat(), expires_at.isoformat()),
                    )
                await self._db.commit()
                return IdempotencyClaim(outcome=IdempotencyOutcome.FRESH)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    IDEMPOTENCY_PERSISTENCE_ERROR,
                    operation="claim",
                    scope=scope,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to claim idempotency key"
                raise QueryError(msg) from exc

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
    ) -> None:
        """Mark a claimed key as ``COMPLETED`` and persist the response."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'completed', response_body = ?, "
                    "response_hash = ? "
                    "WHERE scope = ? AND key = ?",
                    (response_body, response_hash, scope, key),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    IDEMPOTENCY_PERSISTENCE_ERROR,
                    operation="complete",
                    scope=scope,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to record idempotency completion"
                raise QueryError(msg) from exc

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> None:
        """Mark a claimed key as ``FAILED`` so future retries can re-claim."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    "UPDATE idempotency_keys SET status = 'failed' "
                    "WHERE scope = ? AND key = ?",
                    (scope, key),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    IDEMPOTENCY_PERSISTENCE_ERROR,
                    operation="fail",
                    scope=scope,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to record idempotency failure"
                raise QueryError(msg) from exc

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        """Fetch the persisted record verbatim, or None when absent."""
        try:
            cursor = await self._db.execute(
                "SELECT scope, key, status, response_hash, response_body, "
                "created_at, expires_at FROM idempotency_keys "
                "WHERE scope = ? AND key = ?",
                (scope, key),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="get",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to fetch idempotency key"
            raise QueryError(msg) from exc

        if row is None:
            return None
        return IdempotencyRecord(
            scope=NotBlankStr(str(row["scope"])),
            key=NotBlankStr(str(row["key"])),
            status=IdempotencyOutcome(str(row["status"])),
            response_hash=row["response_hash"],
            response_body=row["response_body"],
            created_at=_parse_dt(row["created_at"]),
            expires_at=_parse_dt(row["expires_at"]),
        )

    async def cleanup_expired(self, now: datetime) -> int:
        """Delete expired rows and return the count removed."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM idempotency_keys WHERE expires_at <= ?",
                    (now.isoformat(),),
                )
                await self._db.commit()
                return int(cursor.rowcount or 0)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    IDEMPOTENCY_PERSISTENCE_ERROR,
                    operation="cleanup_expired",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to cleanup expired idempotency keys"
                raise QueryError(msg) from exc
