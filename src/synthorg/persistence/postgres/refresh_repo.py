"""Postgres-backed refresh token repository.

Refresh tokens are opaque strings stored as HMAC-SHA256 hashes.
Each token is single-use: consuming it atomically marks it as used
and returns the associated session/user info for re-issuance.
"""

from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from psycopg import Error as PsycopgError

from synthorg.api.auth.refresh_record import (
    RefreshConsumeOutcome,
    RefreshRecord,
    RefreshRejectReason,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUTH_REFRESH_PERSISTENCE_ERROR,
)
from synthorg.persistence._shared import normalize_utc

# Persistence-boundary rule (#1599): SECURITY_AUTH_REFRESH_* events are
# auth decisions, not storage facts. Repos must not emit them; the
# service / controller layer that calls ``consume`` /
# ``revoke_by_session`` / ``revoke_by_user`` is responsible for
# translating the return value into the appropriate
# ``security.auth.refresh_*`` audit event.

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``."""
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


class _SessionRevokedError(Exception):
    """Internal sentinel: rollback the consume() transaction.

    Raised inside the ``conn.transaction()`` context when the
    revocation callback reports that the session has been revoked --
    propagation triggers psycopg's automatic transaction rollback so
    the UPDATE that marked the token as used does not commit. Caught
    by :meth:`PostgresRefreshTokenRepository.consume` to return a
    SESSION_REVOKED outcome without a second SQL round-trip.
    """


def _raise_session_revoked() -> None:
    """Hoist the sentinel raise out of ``consume()`` (TRY301)."""
    raise _SessionRevokedError


class PostgresRefreshTokenRepository:
    """Postgres-backed refresh token repository.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dict_row = _import_dict_row()

    async def create(
        self,
        token_hash: str,
        session_id: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Store a new refresh token.

        Datetimes are normalised to UTC before insertion so a caller
        that supplied an aware non-UTC datetime cannot poison the
        TIMESTAMPTZ column with an off-zone value the cleanup /
        consume paths interpret incorrectly.
        """
        now = normalize_utc(datetime.now(UTC))
        utc_expires_at = normalize_utc(expires_at)
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "INSERT INTO refresh_tokens "
                    "(token_hash, session_id, user_id, expires_at, "
                    "used, created_at) "
                    "VALUES (%s, %s, %s, %s, FALSE, %s)",
                    (
                        token_hash,
                        session_id,
                        user_id,
                        utc_expires_at,
                        now,
                    ),
                )
        except PsycopgError as exc:
            logger.warning(
                API_AUTH_REFRESH_PERSISTENCE_ERROR,
                operation="create",
                token_hash=token_hash[:8],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to persist refresh token"
            raise QueryError(msg) from exc

    async def consume(
        self,
        token_hash: str,
        *,
        is_session_revoked: Callable[[str], bool] | None = None,
    ) -> RefreshConsumeOutcome:
        """Atomically consume a refresh token (single-use rotation).

        ``is_session_revoked`` is consulted INSIDE the same transaction
        that performs the UPDATE -- if it returns True (or raises), an
        internal sentinel exception unwinds the transaction so the
        token stays unused. A post-commit check would burn the token
        even on a transient revocation-store error, leaving the user
        with no recovery path except a full re-authentication.
        """
        dict_row = self._dict_row
        now = datetime.now(UTC)

        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "UPDATE refresh_tokens SET used = TRUE "
                    "WHERE token_hash = %s AND used = FALSE "
                    "AND expires_at > %s "
                    "RETURNING token_hash, session_id, user_id, "
                    "expires_at, used, created_at",
                    (token_hash, now),
                )
                row = await cur.fetchone()
                if (
                    row is not None
                    and is_session_revoked is not None
                    and (is_session_revoked(row["session_id"]))
                ):
                    # Roll back the UPDATE so the token stays unused;
                    # raising the sentinel here lets ``conn.transaction``
                    # auto-rollback without a second cursor.execute().
                    _raise_session_revoked()
                replay_row: dict[str, Any] | None = None
                if row is None:
                    await cur.execute(
                        "SELECT used FROM refresh_tokens WHERE token_hash = %s",
                        (token_hash,),
                    )
                    replay_row = await cur.fetchone()
        except _SessionRevokedError:
            return RefreshConsumeOutcome(
                reject_reason=RefreshRejectReason.SESSION_REVOKED,
            )
        except PsycopgError as exc:
            logger.warning(
                API_AUTH_REFRESH_PERSISTENCE_ERROR,
                operation="consume",
                token_hash=token_hash[:8],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to consume refresh token"
            raise QueryError(msg) from exc

        if row is not None:
            # Postgres TIMESTAMPTZ rows can come back in the session
            # zone; normalise to UTC at the boundary so the
            # ``RefreshRecord`` always carries a UTC-anchored
            # datetime regardless of server / pool timezone settings.
            return RefreshConsumeOutcome(
                record=RefreshRecord(
                    token_hash=row["token_hash"],
                    session_id=row["session_id"],
                    user_id=row["user_id"],
                    expires_at=normalize_utc(row["expires_at"]),
                    used=bool(row["used"]),
                    created_at=normalize_utc(row["created_at"]),
                ),
            )

        if replay_row is not None and replay_row["used"]:
            return RefreshConsumeOutcome(
                reject_reason=RefreshRejectReason.REPLAY_DETECTED,
            )
        return RefreshConsumeOutcome(
            reject_reason=RefreshRejectReason.NOT_FOUND_OR_EXPIRED,
        )

    async def revoke_by_session(self, session_id: str) -> int:
        """Mark all refresh tokens for a session as used.

        Caller logs ``SECURITY_AUTH_REFRESH_REVOKED`` when count > 0
        (persistence-boundary rule -- repos do not emit decision
        events).
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "UPDATE refresh_tokens SET used = TRUE "
                    "WHERE session_id = %s AND used = FALSE",
                    (session_id,),
                )
                return cur.rowcount
        except PsycopgError as exc:
            logger.warning(
                API_AUTH_REFRESH_PERSISTENCE_ERROR,
                operation="revoke_by_session",
                session_id=session_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to revoke refresh tokens for session {session_id!r}"
            raise QueryError(msg) from exc

    async def revoke_by_user(self, user_id: str) -> int:
        """Mark all refresh tokens for a user as used.

        Caller logs ``SECURITY_AUTH_REFRESH_REVOKED`` when count > 0
        (persistence-boundary rule -- repos do not emit decision
        events).
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "UPDATE refresh_tokens SET used = TRUE "
                    "WHERE user_id = %s AND used = FALSE",
                    (user_id,),
                )
                return cur.rowcount
        except PsycopgError as exc:
            logger.warning(
                API_AUTH_REFRESH_PERSISTENCE_ERROR,
                operation="revoke_by_user",
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to revoke refresh tokens for user {user_id!r}"
            raise QueryError(msg) from exc

    async def cleanup_expired(self) -> int:
        """Remove expired tokens.

        Caller (the periodic cleanup job in
        :mod:`synthorg.api.lifecycle_helpers`) logs
        ``API_AUTH_REFRESH_CLEANUP`` when count > 0; this repo only
        returns the count per the persistence-boundary rule
        (#1599 -- repositories do not emit operational events).
        """
        now = normalize_utc(datetime.now(UTC))
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "DELETE FROM refresh_tokens WHERE expires_at <= %s",
                    (now,),
                )
                return cur.rowcount
        except PsycopgError as exc:
            logger.warning(
                API_AUTH_REFRESH_PERSISTENCE_ERROR,
                operation="cleanup_expired",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to cleanup expired refresh tokens"
            raise QueryError(msg) from exc
