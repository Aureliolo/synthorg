"""Postgres-backed refresh token repository.

Refresh tokens are opaque strings stored as HMAC-SHA256 hashes.
Each token is single-use: consuming it atomically marks it as used
and returns the associated session/user info for re-issuance.
"""

from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.api.auth.refresh_record import RefreshRecord
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_REFRESH_CLEANUP,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_REFRESH_CONSUMED,
    SECURITY_AUTH_REFRESH_REJECTED,
    SECURITY_AUTH_REFRESH_REVOKED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``."""
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


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
        """Store a new refresh token."""
        now = datetime.now(UTC)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO refresh_tokens "
                "(token_hash, session_id, user_id, expires_at, "
                "used, created_at) "
                "VALUES (%s, %s, %s, %s, FALSE, %s)",
                (
                    token_hash,
                    session_id,
                    user_id,
                    expires_at,
                    now,
                ),
            )

    async def consume(
        self,
        token_hash: str,
        *,
        is_session_revoked: Callable[[str], bool] | None = None,
    ) -> RefreshRecord | None:
        """Atomically consume a refresh token (single-use rotation)."""
        dict_row = self._dict_row
        now = datetime.now(UTC)

        async with (
            self._pool.connection() as conn,
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
            replay_row: dict[str, Any] | None = None
            if row is None:
                await cur.execute(
                    "SELECT used FROM refresh_tokens WHERE token_hash = %s",
                    (token_hash,),
                )
                replay_row = await cur.fetchone()

        if row is not None:
            if is_session_revoked and is_session_revoked(
                row["session_id"],
            ):
                logger.warning(
                    SECURITY_AUTH_REFRESH_REJECTED,
                    reason="session_revoked",
                    session_id=row["session_id"][:8],
                )
                return None
            logger.info(
                SECURITY_AUTH_REFRESH_CONSUMED,
                session_id=row["session_id"],
                user_id=row["user_id"],
            )
            return RefreshRecord(
                token_hash=row["token_hash"],
                session_id=row["session_id"],
                user_id=row["user_id"],
                expires_at=row["expires_at"],
                used=bool(row["used"]),
                created_at=row["created_at"],
            )

        if replay_row is not None and replay_row["used"]:
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason="replay_detected",
                token_hash=token_hash[:8],
            )
        else:
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason="not_found_or_expired",
                token_hash=token_hash[:8],
            )
        return None

    async def revoke_by_session(self, session_id: str) -> int:
        """Mark all refresh tokens for a session as used."""
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE refresh_tokens SET used = TRUE "
                "WHERE session_id = %s AND used = FALSE",
                (session_id,),
            )
            count = cur.rowcount
        if count:
            logger.info(
                SECURITY_AUTH_REFRESH_REVOKED,
                session_id=session_id,
                revoked=count,
            )
        return count

    async def revoke_by_user(self, user_id: str) -> int:
        """Mark all refresh tokens for a user as used."""
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE refresh_tokens SET used = TRUE "
                "WHERE user_id = %s AND used = FALSE",
                (user_id,),
            )
            count = cur.rowcount
        if count:
            logger.info(
                SECURITY_AUTH_REFRESH_REVOKED,
                user_id=user_id,
                revoked=count,
            )
        return count

    async def cleanup_expired(self) -> int:
        """Remove expired tokens."""
        now = datetime.now(UTC)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM refresh_tokens WHERE expires_at <= %s",
                (now,),
            )
            count = cur.rowcount
        if count:
            logger.info(API_AUTH_REFRESH_CLEANUP, removed=count)
        return count
