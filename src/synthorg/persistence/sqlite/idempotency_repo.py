"""SQLite-backed idempotency-key repository (#1599).

Atomic claim semantics rely on ``INSERT OR IGNORE`` followed by an
``UPDATE`` of any pre-existing row that has expired or previously
failed. The shared backend ``write_lock`` serialises writes so the
discriminator returned to the caller never races against a concurrent
claim of the same ``(scope, key)``.
"""

import asyncio
import contextlib
import secrets
import sqlite3
from datetime import datetime  # noqa: TC003

import aiosqlite
from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.idempotency import (
    IDEMPOTENCY_PERSISTENCE_ERROR,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
)

logger = get_logger(__name__)


def _parse_dt(value: str) -> datetime:
    """Parse a stored ISO-8601 timestamp.

    Delegates to :func:`parse_iso_utc` so naive values fail-fast and
    every read returns a timezone-aware UTC datetime, matching the
    repository-wide marshalling contract from ``_shared``.
    """
    return parse_iso_utc(value)


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
        now: AwareDatetime,
    ) -> IdempotencyClaim:
        """Atomically claim ``(scope, key)`` for *ttl_seconds*.

        Holds ``self._write_lock`` (asyncio) plus a ``BEGIN IMMEDIATE``
        DB-level RESERVED write lock so competing claimants on
        *different* aiosqlite connections (e.g. a sibling repository
        sharing the pool) serialise -- otherwise each would perform
        an un-locked SELECT and then race on the UPDATE/INSERT.

        Both the lock acquisition (``BEGIN IMMEDIATE``) and the
        subsequent SELECT/UPDATE/INSERT/commit run inside a single
        ``try`` so any exception -- not just one raised after BEGIN
        succeeded -- routes through the rollback + structured-log +
        ``QueryError`` path.
        """
        from datetime import timedelta  # noqa: PLC0415

        expires_at = now + timedelta(seconds=ttl_seconds)
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_idempotency_row(scope, key)
                claim = await self._claim_under_lock(
                    scope=scope,
                    key=key,
                    row=row,
                    now=now,
                    expires_at=expires_at,
                )
                await self._db.commit()
            except MemoryError, RecursionError:
                # System errors must propagate without touching the
                # row -- attempting rollback under OOM may itself fail.
                raise
            except Exception as exc:
                # Catch broadly so a non-SQL failure (parse_iso_utc on
                # a corrupt row, IdempotencyClaim model_validator,
                # ...) still triggers rollback + structured logging
                # rather than skipping straight past the gate. SQL
                # errors stay wrapped in QueryError to preserve the
                # existing exception contract; everything else
                # re-raises verbatim so its semantic is not lost.
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    IDEMPOTENCY_PERSISTENCE_ERROR,
                    operation="claim",
                    scope=scope,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                if isinstance(exc, sqlite3.Error | aiosqlite.Error):
                    msg = "Failed to claim idempotency key"
                    raise QueryError(msg) from exc
                raise
        return claim

    async def _fetch_idempotency_row(
        self,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> aiosqlite.Row | None:
        """Return the existing row for *(scope, key)* or ``None``."""
        cursor = await self._db.execute(
            "SELECT status, response_body, expires_at "
            "FROM idempotency_keys WHERE scope = ? AND key = ?",
            (scope, key),
        )
        return await cursor.fetchone()

    async def _claim_under_lock(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        row: aiosqlite.Row | None,
        now: AwareDatetime,
        expires_at: AwareDatetime,
    ) -> IdempotencyClaim:
        """Pick the right claim outcome given the existing *row*."""
        if row is not None:
            status = str(row["status"])
            row_expires = _parse_dt(row["expires_at"])
            if row_expires > now and status == "completed":
                return IdempotencyClaim(
                    outcome=IdempotencyOutcome.COMPLETED,
                    cached_response=row["response_body"],
                )
            if row_expires > now and status == "in_flight":
                return IdempotencyClaim(outcome=IdempotencyOutcome.IN_FLIGHT)
            # Expired OR failed -- rotate the lease.
            new_token = secrets.token_hex(16)
            await self._update_row_to_in_flight(
                scope=scope,
                key=key,
                new_token=new_token,
                expires_at=expires_at,
            )
            return IdempotencyClaim(
                outcome=IdempotencyOutcome.FRESH,
                claim_token=NotBlankStr(new_token),
            )
        new_token = secrets.token_hex(16)
        await self._insert_in_flight_row(
            scope=scope,
            key=key,
            new_token=new_token,
            now=now,
            expires_at=expires_at,
        )
        return IdempotencyClaim(
            outcome=IdempotencyOutcome.FRESH,
            claim_token=NotBlankStr(new_token),
        )

    async def _update_row_to_in_flight(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        new_token: str,
        expires_at: AwareDatetime,
    ) -> None:
        """Rotate an expired/failed row to a fresh in-flight lease.

        ``created_at`` is intentionally NOT in the SET clause: it
        records the original insertion time so
        ``IdempotencyRecord.created_at`` stays meaningful across
        re-claims (the protocol contract). Only the lease columns
        rotate.
        """
        await self._db.execute(
            "UPDATE idempotency_keys "
            "SET status = 'in_flight', claim_token = ?, "
            "response_hash = NULL, response_body = NULL, "
            "expires_at = ? "
            "WHERE scope = ? AND key = ?",
            (
                new_token,
                format_iso_utc(expires_at),
                scope,
                key,
            ),
        )

    async def _insert_in_flight_row(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        new_token: str,
        now: AwareDatetime,
        expires_at: AwareDatetime,
    ) -> None:
        """Insert a fresh in-flight idempotency row."""
        await self._db.execute(
            "INSERT INTO idempotency_keys "
            "(scope, key, status, claim_token, "
            "created_at, expires_at) "
            "VALUES (?, ?, 'in_flight', ?, ?, ?)",
            (
                scope,
                key,
                new_token,
                format_iso_utc(now),
                format_iso_utc(expires_at),
            ),
        )

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
        claim_token: NotBlankStr,
    ) -> bool:
        """Mark a claimed key as ``COMPLETED`` if *claim_token* matches.

        Returns ``True`` when the row's stored token matched and the
        UPDATE landed; ``False`` when the lease has rotated (a stale
        worker MUST NOT recover by ignoring this -- the new lease
        owns the row).
        """
        async with self._write_lock:
            try:
                # Gate on status = 'in_flight' so a stale worker cannot
                # flip an already-completed row -- completed rows MUST
                # remain immutable. Token CAS alone would let a slow
                # callback whose lease coincidentally re-issued the
                # same token overwrite the new lease's response.
                cursor = await self._db.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'completed', response_body = ?, "
                    "response_hash = ? "
                    "WHERE scope = ? AND key = ? AND claim_token = ? "
                    "AND status = 'in_flight'",
                    (response_body, response_hash, scope, key, claim_token),
                )
                await self._db.commit()
                rowcount = cursor.rowcount
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
        return rowcount > 0

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        claim_token: NotBlankStr,
    ) -> bool:
        """Mark a claimed key as ``FAILED`` if *claim_token* matches."""
        async with self._write_lock:
            try:
                # Same status gate as ``complete``: only an in-flight
                # row owned by the matching lease can transition to
                # failed.
                cursor = await self._db.execute(
                    "UPDATE idempotency_keys SET status = 'failed' "
                    "WHERE scope = ? AND key = ? AND claim_token = ? "
                    "AND status = 'in_flight'",
                    (scope, key, claim_token),
                )
                await self._db.commit()
                rowcount = cursor.rowcount
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
        return rowcount > 0

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
        # Construction is wrapped so decode / validation failures
        # (corrupt status enum value, naive ISO timestamp via
        # ``parse_iso_utc``, IdempotencyRecord model validator
        # rejection) route through the same QueryError contract as
        # the SQL path. Callers see one exception type for any read
        # failure regardless of cause.
        try:
            return IdempotencyRecord(
                scope=NotBlankStr(str(row["scope"])),
                key=NotBlankStr(str(row["key"])),
                status=IdempotencyOutcome(str(row["status"])),
                response_hash=row["response_hash"],
                response_body=row["response_body"],
                created_at=_parse_dt(row["created_at"]),
                expires_at=_parse_dt(row["expires_at"]),
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="get",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to fetch idempotency key"
            raise QueryError(msg) from exc

    async def cleanup_expired(self, now: AwareDatetime) -> int:
        """Delete expired rows and return the count removed."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM idempotency_keys WHERE expires_at <= ?",
                    (format_iso_utc(now),),
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
