"""SQLite-backed refresh token repository.

Refresh tokens are opaque strings stored as HMAC-SHA256 hashes.
Each token is single-use: consuming it atomically marks it as used
and returns the associated session/user info for re-issuance.
"""

import asyncio
import contextlib
import sqlite3
from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime

import aiosqlite

from synthorg.core.auth.refresh_record import (
    RefreshConsumeOutcome,
    RefreshRecord,
    RefreshRejectReason,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUTH_REFRESH_PERSISTENCE_ERROR,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc

# Persistence-boundary rule: SECURITY_AUTH_REFRESH_* events are
# auth decisions, not storage facts. Repos must not emit them; the
# service / controller layer that calls ``consume`` /
# ``revoke_by_session`` / ``revoke_by_user`` is responsible for
# translating the return value into the appropriate
# ``security.auth.refresh_*`` audit event.

logger = get_logger(__name__)


class SQLiteRefreshTokenRepository:
    """SQLite-backed refresh token repository.

    Args:
        db: Open aiosqlite connection with ``row_factory`` set.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def create(
        self,
        token_hash: str,
        session_id: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Store a new refresh token."""
        now = datetime.now(UTC)
        async with self._write_lock:
            try:
                await self._db.execute(
                    "INSERT INTO refresh_tokens "
                    "(token_hash, session_id, user_id, expires_at, "
                    "used, created_at) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (
                        token_hash,
                        session_id,
                        user_id,
                        format_iso_utc(expires_at),
                        format_iso_utc(now),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to persist refresh token"
                # Storage failure -- NOT an auth decision; emit under
                # api.* so the cryptographic audit chain is not polluted
                # with DB errors.
                logger.warning(
                    API_AUTH_REFRESH_PERSISTENCE_ERROR,
                    operation="create",
                    session_id=session_id,
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def consume(
        self,
        token_hash: str,
        *,
        is_session_revoked: Callable[[str], bool] | None = None,
    ) -> RefreshConsumeOutcome:
        """Atomically consume a refresh token (single-use rotation).

        ``is_session_revoked`` is consulted while the write lock is
        still held and BEFORE the UPDATE commits. A transient failure
        in the callback (or a True return on a revoked session) rolls
        back the UPDATE so the token stays unused -- otherwise a
        post-commit check would burn the token even on a transient
        revocation-store error, leaving the user with no path to
        recover except a full re-authentication.
        """
        now = format_iso_utc(datetime.now(UTC))
        revoked = False
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "UPDATE refresh_tokens SET used = 1 "
                    "WHERE token_hash = ? AND used = 0 AND expires_at > ? "
                    "RETURNING token_hash, session_id, user_id, "
                    "expires_at, used, created_at",
                    (token_hash, now),
                )
                row = await cursor.fetchone()
                if row is not None and is_session_revoked is not None:
                    revoked = is_session_revoked(row["session_id"])
                if revoked:
                    # Session is gone -- token must stay unused so a
                    # later legitimate session can still consume it.
                    await self._db.rollback()
                else:
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to consume refresh token"
                logger.warning(
                    API_AUTH_REFRESH_PERSISTENCE_ERROR,
                    operation="consume",
                    token_hash=token_hash[:8],
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            except Exception:
                # The revocation callback is operator-supplied and may
                # raise anything (e.g. session-store transient error).
                # Rollback so the token is not consumed on a failed
                # check, then re-raise verbatim so the caller sees
                # the original error class.
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                raise

        if revoked:
            return RefreshConsumeOutcome(
                reject_reason=RefreshRejectReason.SESSION_REVOKED,
            )
        if row is not None:
            return RefreshConsumeOutcome(
                record=RefreshRecord(
                    token_hash=row["token_hash"],
                    session_id=row["session_id"],
                    user_id=row["user_id"],
                    expires_at=parse_iso_utc(row["expires_at"]),
                    used=bool(row["used"]),
                    created_at=parse_iso_utc(row["created_at"]),
                ),
            )

        # No row consumed -- determine why so the caller can emit
        # SECURITY_AUTH_REFRESH_REJECTED with the right reason.
        check = await self._db.execute(
            "SELECT used FROM refresh_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        replay_row = await check.fetchone()
        if replay_row is not None and replay_row["used"]:
            return RefreshConsumeOutcome(
                reject_reason=RefreshRejectReason.REPLAY_DETECTED,
            )
        return RefreshConsumeOutcome(
            reject_reason=RefreshRejectReason.NOT_FOUND_OR_EXPIRED,
        )

    async def revoke_by_session(self, session_id: str) -> int:
        """Mark all refresh tokens for a session as used."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "UPDATE refresh_tokens SET used = 1 "
                    "WHERE session_id = ? AND used = 0",
                    (session_id,),
                )
                await self._db.commit()
                count = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to revoke refresh tokens for session {session_id!r}"
                logger.warning(
                    API_AUTH_REFRESH_PERSISTENCE_ERROR,
                    operation="revoke_by_session",
                    session_id=session_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        # Caller logs SECURITY_AUTH_REFRESH_REVOKED when count > 0;
        # repo only returns the count.
        return count

    async def revoke_by_user(self, user_id: str) -> int:
        """Mark all refresh tokens for a user as used."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "UPDATE refresh_tokens SET used = 1 WHERE user_id = ? AND used = 0",
                    (user_id,),
                )
                await self._db.commit()
                count = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to revoke refresh tokens for user {user_id!r}"
                logger.warning(
                    API_AUTH_REFRESH_PERSISTENCE_ERROR,
                    operation="revoke_by_user",
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        # Caller logs SECURITY_AUTH_REFRESH_REVOKED when count > 0;
        # repo only returns the count.
        return count

    async def cleanup_expired(self) -> int:
        """Remove expired tokens.

        Caller (the periodic cleanup job in
        :mod:`synthorg.api.lifecycle_helpers`) logs
        ``API_AUTH_REFRESH_CLEANUP`` when count > 0; this repo only
        returns the count per the persistence-boundary rule:
        repositories do not emit operational events.
        """
        now = format_iso_utc(datetime.now(UTC))
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM refresh_tokens WHERE expires_at <= ?",
                    (now,),
                )
                await self._db.commit()
                rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    API_AUTH_REFRESH_PERSISTENCE_ERROR,
                    operation="cleanup_expired",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to cleanup expired refresh tokens"
                raise QueryError(msg) from exc
        return rowcount
