"""Postgres-backed session repository.

Uses the shared ``AsyncConnectionPool`` every Postgres repo composes
against.  Each operation checks out a connection via ``async with
pool.connection() as conn``; the context manager auto-commits on
clean exit and rolls back on exception.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.api.auth.session import Session
from synthorg.api.guards import HumanRole
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_SESSION_CLEANUP,
)
from synthorg.observability.events.security import (
    SECURITY_SESSION_LIMIT_ENFORCED,
    SECURITY_SESSION_REVOKED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``.

    Kept out of the module-level import block so Sqlite-only deployments
    never need the optional ``psycopg`` dependency at import time.
    """
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


def _row_to_session(row: Any) -> Session:
    """Deserialize a psycopg dict row into a :class:`Session`."""
    return Session(
        session_id=NotBlankStr(row["session_id"]),
        user_id=NotBlankStr(row["user_id"]),
        username=NotBlankStr(row["username"]),
        role=HumanRole(row["role"]),
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
        expires_at=row["expires_at"],
        revoked=bool(row["revoked"]),
    )


class PostgresSessionRepository:
    """Postgres-backed hybrid session repository.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._revoked: set[str] = set()
        self._dict_row = _import_dict_row()

    async def load_revoked(self) -> None:
        """Load revoked session IDs from Postgres into memory."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT session_id FROM sessions "
                "WHERE revoked = TRUE AND expires_at > %s",
                (now,),
            )
            rows = await cur.fetchall()
        self._revoked = {row["session_id"] for row in rows}

    async def create(self, session: Session) -> None:
        """Persist a new session."""
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO sessions "
                "(session_id, user_id, username, role, ip_address, "
                "user_agent, created_at, last_active_at, expires_at, "
                "revoked) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session.session_id,
                    session.user_id,
                    session.username,
                    session.role.value,
                    session.ip_address,
                    session.user_agent,
                    session.created_at,
                    session.last_active_at,
                    session.expires_at,
                    session.revoked,
                ),
            )
        if session.revoked:
            self._revoked.add(session.session_id)

    async def get(self, session_id: str) -> Session | None:
        """Look up a session by ID."""
        dict_row = self._dict_row

        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        return _row_to_session(row) if row else None

    async def list_by_user(self, user_id: str) -> tuple[Session, ...]:
        """List active (non-expired, non-revoked) sessions for a user."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM sessions "
                "WHERE user_id = %s AND revoked = FALSE "
                "AND expires_at > %s "
                "ORDER BY created_at DESC",
                (user_id, now),
            )
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def list_all(self) -> tuple[Session, ...]:
        """List all active (non-expired, non-revoked) sessions."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM sessions "
                "WHERE revoked = FALSE AND expires_at > %s "
                "ORDER BY created_at DESC",
                (now,),
            )
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def revoke(self, session_id: str) -> bool:
        """Revoke a session by ID."""
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE sessions SET revoked = TRUE "
                "WHERE session_id = %s AND revoked = FALSE",
                (session_id,),
            )
            count = cur.rowcount
        if count > 0:
            self._revoked.add(session_id)
            logger.info(SECURITY_SESSION_REVOKED, session_id=session_id)
            return True
        return False

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all active sessions for a user."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE sessions SET revoked = TRUE "
                    "WHERE user_id = %s AND revoked = FALSE "
                    "AND expires_at > %s",
                    (user_id, now),
                )
                count = cur.rowcount
            if count == 0:
                return 0
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE user_id = %s AND revoked = TRUE "
                    "AND expires_at > %s",
                    (user_id, now),
                )
                rows = await cur.fetchall()
        self._revoked.update(row["session_id"] for row in rows)
        logger.info(SECURITY_SESSION_REVOKED, user_id=user_id, count=count)
        return count

    async def enforce_session_limit(
        self,
        user_id: str,
        max_sessions: int,
    ) -> int:
        """Revoke oldest sessions if user exceeds the concurrent limit."""
        if max_sessions <= 0:
            return 0
        active = await self.list_by_user(user_id)
        excess = len(active) - max_sessions
        if excess <= 0:
            return 0
        to_revoke = active[-excess:]
        revoked = 0
        for session in to_revoke:
            if await self.revoke(session.session_id):
                revoked += 1
        if revoked:
            logger.info(
                SECURITY_SESSION_LIMIT_ENFORCED,
                user_id=user_id,
                revoked=revoked,
                max_sessions=max_sessions,
            )
        return revoked

    def is_revoked(self, session_id: str) -> bool:
        """Check whether a session is revoked (sync, O(1))."""
        return session_id in self._revoked

    async def cleanup_expired(self) -> int:
        """Remove expired sessions from the database."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT session_id FROM sessions WHERE expires_at <= %s",
                    (now,),
                )
                rows = await cur.fetchall()
            ids = {row["session_id"] for row in rows}
            if not ids:
                return 0
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM sessions WHERE expires_at <= %s",
                    (now,),
                )
        self._revoked -= ids
        logger.debug(API_SESSION_CLEANUP, removed=len(ids))
        return len(ids)
