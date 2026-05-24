"""SQLite-backed session repository.

Hybrid in-memory + durable session store.  The in-memory revocation
set provides O(1) sync lookups for the auth middleware hot path; the
SQLite connection provides survival across restarts.
"""

import contextlib
import sqlite3
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.session import Session
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUTH_SESSION_PERSISTENCE_ERROR,
    API_SESSION_CLEANUP,
    API_SESSION_CREATE_FAILED,
    API_SESSION_REVOKE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)
from synthorg.persistence.auth_protocol import SessionFilterSpec  # noqa: TC001
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


def _row_to_session(row: Any) -> Session:
    """Deserialize an aiosqlite.Row into a :class:`Session`.

    SQLite stores timestamps as TEXT (``aiosqlite`` returns them as
    ``str``).  ``coerce_row_timestamp`` accepts either ``str`` or
    ``datetime`` so tests that patch the bound ``datetime`` name with
    a ``MagicMock`` (and supply pre-parsed datetime row values) keep
    working alongside production reads.

    Returns:
        Result of type ``Session``.
    """
    return Session(
        session_id=NotBlankStr(row["session_id"]),
        user_id=NotBlankStr(row["user_id"]),
        username=NotBlankStr(row["username"]),
        role=HumanRole(row["role"]),
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        created_at=coerce_row_timestamp(row["created_at"]),
        last_active_at=coerce_row_timestamp(row["last_active_at"]),
        expires_at=coerce_row_timestamp(row["expires_at"]),
        revoked=bool(row["revoked"]),
    )


class SQLiteSessionRepository:
    """SQLite-backed hybrid session repository.

    The ``is_revoked`` method is synchronous and checks a local ``set``;
    it is called on every authenticated request and must not block
    the event loop.

    Args:
        db: Open aiosqlite connection with ``row_factory`` set.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context
        self._revoked: set[str] = set()

    async def load_revoked(self) -> None:
        """Load revoked session IDs from SQLite into memory.

        Called once at startup to restore revocation state.  Only
        loads sessions that have not yet expired -- expired JWTs are
        rejected by the decoder regardless of revocation.
        """
        now = format_iso_utc(datetime.now(UTC))
        cursor = await self._db.execute(
            "SELECT session_id FROM sessions WHERE revoked = 1 AND expires_at > ?",
            (now,),
        )
        rows = await cursor.fetchall()
        self._revoked = {row["session_id"] for row in rows}

    async def save(self, entity: Session) -> None:
        """Persist a session (insert or update by session_id).

        Raises:
            QueryError: If the database query fails.
        """
        session = entity
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT INTO sessions "
                    "(session_id, user_id, username, role, ip_address, "
                    "user_agent, created_at, last_active_at, expires_at, "
                    "revoked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "user_id = excluded.user_id, "
                    "username = excluded.username, "
                    "role = excluded.role, "
                    "ip_address = excluded.ip_address, "
                    "user_agent = excluded.user_agent, "
                    "last_active_at = excluded.last_active_at, "
                    "expires_at = excluded.expires_at, "
                    "revoked = excluded.revoked",
                    (
                        session.session_id,
                        session.user_id,
                        session.username,
                        session.role.value,
                        session.ip_address,
                        session.user_agent,
                        format_iso_utc(session.created_at),
                        format_iso_utc(session.last_active_at),
                        format_iso_utc(session.expires_at),
                        int(session.revoked),
                    ),
                )
                await self._db.commit()
                if session.revoked:
                    self._revoked.add(session.session_id)
                else:
                    # Refresh-via-save clears prior revocation; keep
                    # the in-memory cache in lockstep with the
                    # persisted ``revoked`` flag.
                    self._revoked.discard(session.session_id)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to persist session {session.session_id!r}"
                logger.warning(
                    API_SESSION_CREATE_FAILED,
                    session_id=session.session_id,
                    user_id=session.user_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: str) -> Session | None:
        """Look up a session by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (entity_id,),
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List all sessions with pagination.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=API_AUTH_SESSION_PERSISTENCE_ERROR
        )
        sql = "SELECT * FROM sessions ORDER BY session_id ASC"
        params: tuple[object, ...] = ()
        sql += " LIMIT ? OFFSET ?"
        params = (*params, limit, offset)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def query(
        self,
        filter_spec: SessionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions matching the filter spec.

        Returns:
            Tuple of (items, next_cursor) for paginated iteration.
        """
        limit = validate_pagination_args(
            limit, offset, event=API_AUTH_SESSION_PERSISTENCE_ERROR
        )
        sql = "SELECT * FROM sessions WHERE 1=1"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = ?"
            params.append(filter_spec.user_id)
        if filter_spec.revoked is not None:
            sql += " AND revoked = ?"
            params.append(int(filter_spec.revoked))
        sql += " ORDER BY session_id ASC"
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]
        cursor = await self._db.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def count(self, filter_spec: SessionFilterSpec) -> int:
        """Count sessions matching the filter spec.

        Returns:
            Number of matching rows.
        """
        sql = "SELECT COUNT(*) AS cnt FROM sessions WHERE 1=1"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = ?"
            params.append(filter_spec.user_id)
        if filter_spec.revoked is not None:
            sql += " AND revoked = ?"
            params.append(int(filter_spec.revoked))
        cursor = await self._db.execute(sql, tuple(params))
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def list_by_user(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List active (non-expired, non-revoked) sessions for a user.

        Returns:
            The matching entities.
        """
        now = format_iso_utc(datetime.now(UTC))
        sql = (
            "SELECT * FROM sessions "
            "WHERE user_id = ? AND revoked = 0 "
            "AND expires_at > ? "
            "ORDER BY created_at DESC, session_id ASC"
        )
        params: tuple[object, ...] = (user_id, now)
        effective_offset = max(0, int(offset))
        sql += " LIMIT ? OFFSET ?"
        params = (*params, int(limit), effective_offset)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def list_all(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List all active (non-expired, non-revoked) sessions.

        Returns:
            The matching entities.
        """
        now = format_iso_utc(datetime.now(UTC))
        sql = (
            "SELECT * FROM sessions "
            "WHERE revoked = 0 AND expires_at > ? "
            "ORDER BY created_at DESC, session_id ASC"
        )
        params: tuple[object, ...] = (now,)
        effective_offset = max(0, int(offset))
        sql += " LIMIT ? OFFSET ?"
        params = (*params, int(limit), effective_offset)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return tuple(_row_to_session(r) for r in rows)

    async def delete(self, entity_id: str) -> bool:
        """Delete a session by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM sessions WHERE session_id = ?",
                    (entity_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete session {entity_id!r}"
                logger.warning(
                    API_SESSION_REVOKE_FAILED,
                    session_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            else:
                if cursor.rowcount > 0:
                    # Otherwise ``is_revoked`` keeps reporting a
                    # deleted session as revoked until the next
                    # ``load_revoked``.
                    self._revoked.discard(entity_id)
                    return True
                return False

    async def revoke(self, session_id: str) -> bool:
        """Revoke a session by ID.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "UPDATE sessions SET revoked = 1 "
                    "WHERE session_id = ? AND revoked = 0",
                    (session_id,),
                )
                await self._db.commit()
                rowcount = cursor.rowcount
                if rowcount > 0:
                    self._revoked.add(session_id)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to revoke session {session_id!r}"
                logger.warning(
                    API_SESSION_REVOKE_FAILED,
                    session_id=session_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        # Audit logging lives above persistence so the SECURITY_SESSION_REVOKED
        # event reflects the authorization decision rather than a
        # storage-only update; the caller (service/controller) owns the
        # emit.
        return rowcount > 0

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all active sessions for a user.

        Captures the session-id snapshot BEFORE committing the UPDATE
        so a SELECT failure cannot leave the DB committed-revoked
        while ``self._revoked`` (in-memory set) stays unaware -- a
        partial-success state would route the affected sessions
        through the auth fast path until the next ``load_revoked``.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        now = format_iso_utc(datetime.now(UTC))
        async with self._write_context():
            try:
                # SELECT first: capture the ids that WILL be revoked
                # while they are still pending.  If this read fails we
                # have not yet committed any change.
                cursor = await self._db.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE user_id = ? AND revoked = 0 AND expires_at > ?",
                    (user_id, now),
                )
                rows = await cursor.fetchall()
                if not rows:
                    return 0
                cursor = await self._db.execute(
                    "UPDATE sessions SET revoked = 1 "
                    "WHERE user_id = ? AND revoked = 0 AND expires_at > ?",
                    (user_id, now),
                )
                count = cursor.rowcount
                # Commit only after both the SELECT snapshot and the
                # UPDATE succeeded; in-memory mutation only happens
                # after a successful commit.
                await self._db.commit()
                self._revoked.update(row["session_id"] for row in rows)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to revoke sessions for user {user_id!r}"
                logger.warning(
                    API_SESSION_REVOKE_FAILED,
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        # Audit logging stays above persistence: the caller correlates the
        # bulk-revoke event with the authorization context (which user
        # initiated, why) that this layer does not see.
        return count

    async def enforce_session_limit(
        self,
        user_id: str,
        max_sessions: int,
    ) -> int:
        """Revoke oldest sessions if user exceeds the concurrent limit.

        Returns:
            Numeric result of the operation.
        """
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
        """Check whether a session is revoked (sync, O(1)).

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.
        """
        return session_id in self._revoked

    async def cleanup_expired(self) -> int:
        """Remove expired sessions from the database.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        now = format_iso_utc(datetime.now(UTC))
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "SELECT session_id FROM sessions WHERE expires_at <= ?",
                    (now,),
                )
                rows = await cursor.fetchall()
                ids = {row["session_id"] for row in rows}
                if not ids:
                    return 0
                await self._db.execute(
                    "DELETE FROM sessions WHERE expires_at <= ?",
                    (now,),
                )
                await self._db.commit()
                self._revoked -= ids
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to cleanup expired sessions"
                logger.warning(
                    API_SESSION_CLEANUP,
                    phase="cleanup_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        logger.debug(API_SESSION_CLEANUP, removed=len(ids))
        return len(ids)
