"""Postgres-backed session repository.

Uses the shared ``AsyncConnectionPool`` every Postgres repo composes
against.  Each operation checks out a connection via ``async with
pool.connection() as conn``; the context manager auto-commits on
clean exit and rolls back on exception.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.session import Session
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_SESSION_PERSISTENCE_ERROR,
    API_SESSION_CLEANUP,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)
from synthorg.persistence.auth_protocol import SessionFilterSpec  # noqa: TC001

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

    async def save(self, entity: Session) -> None:
        """Persist a session (insert or update by session_id)."""
        session = entity
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO sessions "
                "(session_id, user_id, username, role, ip_address, "
                "user_agent, created_at, last_active_at, expires_at, "
                "revoked) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "user_id = EXCLUDED.user_id, "
                "username = EXCLUDED.username, "
                "role = EXCLUDED.role, "
                "ip_address = EXCLUDED.ip_address, "
                "user_agent = EXCLUDED.user_agent, "
                "last_active_at = EXCLUDED.last_active_at, "
                "expires_at = EXCLUDED.expires_at, "
                "revoked = EXCLUDED.revoked",
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
        else:
            # Refresh-via-save (re-issued token, etc.) clears prior
            # revocation; keep the in-memory cache in lockstep with
            # the persisted ``revoked`` flag.
            self._revoked.discard(session.session_id)

    async def get(self, entity_id: str) -> Session | None:
        """Look up a session by ID."""
        dict_row = self._dict_row

        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM sessions WHERE session_id = %s",
                (entity_id,),
            )
            row = await cur.fetchone()
        return _row_to_session(row) if row else None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List all sessions with pagination."""
        limit = validate_pagination_args(
            limit, offset, event=API_AUTH_SESSION_PERSISTENCE_ERROR
        )
        dict_row = self._dict_row

        sql = "SELECT * FROM sessions ORDER BY session_id ASC"
        params: tuple[object, ...] = ()
        sql += " LIMIT %s OFFSET %s"
        params = (*params, limit, offset)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def query(
        self,
        filter_spec: SessionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions matching the filter spec."""
        limit = validate_pagination_args(
            limit, offset, event=API_AUTH_SESSION_PERSISTENCE_ERROR
        )
        dict_row = self._dict_row

        sql = "SELECT * FROM sessions WHERE TRUE"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = %s"
            params.append(filter_spec.user_id)
        if filter_spec.revoked is not None:
            sql += " AND revoked = %s"
            params.append(filter_spec.revoked)
        sql += " ORDER BY session_id ASC"
        sql += " LIMIT %s OFFSET %s"
        params = [*params, limit, offset]
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def count(self, filter_spec: SessionFilterSpec) -> int:
        """Count sessions matching the filter spec."""
        dict_row = self._dict_row

        sql = "SELECT COUNT(*) AS cnt FROM sessions WHERE TRUE"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = %s"
            params.append(filter_spec.user_id)
        if filter_spec.revoked is not None:
            sql += " AND revoked = %s"
            params.append(filter_spec.revoked)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, tuple(params))
            row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def list_by_user(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List active (non-expired, non-revoked) sessions for a user."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        sql = (
            "SELECT * FROM sessions "
            "WHERE user_id = %s AND revoked = FALSE "
            "AND expires_at > %s "
            "ORDER BY created_at DESC, session_id ASC"
        )
        params: tuple[object, ...] = (user_id, now)
        effective_offset = max(0, int(offset))
        sql += " LIMIT %s OFFSET %s"
        params = (*params, int(limit), effective_offset)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def list_all(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List all active (non-expired, non-revoked) sessions."""
        dict_row = self._dict_row

        now = datetime.now(UTC)
        sql = (
            "SELECT * FROM sessions "
            "WHERE revoked = FALSE AND expires_at > %s "
            "ORDER BY created_at DESC, session_id ASC"
        )
        params: tuple[object, ...] = (now,)
        effective_offset = max(0, int(offset))
        sql += " LIMIT %s OFFSET %s"
        params = (*params, int(limit), effective_offset)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def delete(self, entity_id: str) -> bool:
        """Delete a session by ID."""
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM sessions WHERE session_id = %s",
                (entity_id,),
            )
            count = cur.rowcount
        if count > 0:
            # Otherwise ``is_revoked`` keeps reporting a deleted
            # session as revoked until the next ``load_revoked``.
            self._revoked.discard(entity_id)
            return True
        return False

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
            # Audit logging lives above persistence so the
            # SECURITY_SESSION_REVOKED event reflects the authorization
            # decision rather than a storage-only update; the caller
            # (service/controller) owns the emit.
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
        # Audit logging stays above persistence: the caller correlates the
        # bulk-revoke event with the authorization context (which user
        # initiated, why) that this layer does not see.
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
        # SECURITY_SESSION_LIMIT_ENFORCED belongs in the caller so the
        # audit entry sits with the policy decision (which limit fired,
        # against which actor) rather than against a storage commit
        # that has no visibility into authorization context.
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
