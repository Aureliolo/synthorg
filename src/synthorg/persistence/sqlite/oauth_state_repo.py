"""SQLite-backed OAuth authorization-state repository.

Persists transient :class:`OAuthState` rows in the ``oauth_states``
table.  States are short-lived (minutes) and consumed once on
callback; ``cleanup_expired`` reclaims stale rows.
"""

import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import OAuthState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.oauth_state import (
    PERSISTENCE_OAUTH_STATE_CLEANUP,
    PERSISTENCE_OAUTH_STATE_CLEANUP_FAILED,
    PERSISTENCE_OAUTH_STATE_DELETE_FAILED,
    PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
    PERSISTENCE_OAUTH_STATE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


_SELECT_COLS = (
    "state_token, connection_name, pkce_verifier, nonce, "
    "scopes_requested, redirect_uri, created_at, expires_at, "
    "consumed_at, connection_name_returned"
)


def _row_to_state(row: aiosqlite.Row) -> OAuthState:
    """Deserialize a row tuple into an :class:`OAuthState`.

    Returns:
        Result of type ``OAuthState``.
    """
    (
        state_token,
        connection_name,
        pkce_verifier,
        nonce,
        scopes_requested,
        redirect_uri,
        created_at,
        expires_at,
        consumed_at,
        connection_name_returned,
    ) = row
    return OAuthState(
        state_token=NotBlankStr(state_token),
        connection_name=NotBlankStr(connection_name),
        pkce_verifier=NotBlankStr(pkce_verifier) if pkce_verifier else None,
        nonce=NotBlankStr(nonce) if nonce else None,
        scopes_requested=scopes_requested or "",
        redirect_uri=redirect_uri or "",
        created_at=coerce_row_timestamp(created_at),
        expires_at=coerce_row_timestamp(expires_at),
        consumed_at=(
            coerce_row_timestamp(consumed_at) if consumed_at is not None else None
        ),
        connection_name_returned=(
            NotBlankStr(connection_name_returned) if connection_name_returned else None
        ),
    )


class SQLiteOAuthStateRepository:
    """SQLite implementation of :class:`OAuthStateRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        """Bind to *db* and serialize writes via *write_context*."""
        self._db = db
        self._write_context = write_context

    async def save(self, state: OAuthState) -> None:
        """Upsert an OAuth state row keyed by ``state_token``.

        Persists every column on ``OAuthState`` including the two
        idempotency markers (``consumed_at`` /
        ``connection_name_returned``). Flow-start callers set both
        to ``None``; post-callback snapshots carry them populated.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """
                    INSERT INTO oauth_states (
                        state_token, connection_name, pkce_verifier, nonce,
                        scopes_requested, redirect_uri, created_at, expires_at,
                        consumed_at, connection_name_returned
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(state_token) DO UPDATE SET
                        connection_name = excluded.connection_name,
                        pkce_verifier = excluded.pkce_verifier,
                        nonce = excluded.nonce,
                        scopes_requested = excluded.scopes_requested,
                        redirect_uri = excluded.redirect_uri,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at,
                        consumed_at = COALESCE(consumed_at, excluded.consumed_at),
                        connection_name_returned = COALESCE(
                            connection_name_returned,
                            excluded.connection_name_returned
                        )
                    """,
                    (
                        str(state.state_token),
                        str(state.connection_name),
                        str(state.pkce_verifier) if state.pkce_verifier else None,
                        str(state.nonce) if state.nonce else None,
                        state.scopes_requested,
                        state.redirect_uri,
                        format_iso_utc(state.created_at),
                        format_iso_utc(state.expires_at),
                        (
                            format_iso_utc(state.consumed_at)
                            if state.consumed_at
                            else None
                        ),
                        (
                            str(state.connection_name_returned)
                            if state.connection_name_returned
                            else None
                        ),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save oauth_state {state.state_token!r}"
                logger.warning(
                    PERSISTENCE_OAUTH_STATE_SAVE_FAILED,
                    state_token=str(state.state_token),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, state_token: NotBlankStr) -> OAuthState | None:
        """Fetch an OAuth state by token.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                f"SELECT {_SELECT_COLS} FROM oauth_states WHERE state_token = ?",  # noqa: S608
                (str(state_token),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch oauth_state {state_token!r}"
            logger.warning(
                PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
                state_token=str(state_token),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_state(row)
        except (ValueError, TypeError, KeyError) as exc:
            msg = f"Failed to deserialize oauth_state {state_token!r}"
            logger.warning(
                PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
                state_token=str(state_token),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, state_token: NotBlankStr) -> bool:
        """Delete an OAuth state; return ``True`` if a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM oauth_states WHERE state_token = ?",
                    (str(state_token),),
                ) as cursor:
                    deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete oauth_state {state_token!r}"
                logger.warning(
                    PERSISTENCE_OAUTH_STATE_DELETE_FAILED,
                    state_token=str(state_token),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OAuthState, ...]:
        """List all OAuth states with pagination.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        if limit is not None and limit <= 0:
            return ()
        sql = f"SELECT {_SELECT_COLS} FROM oauth_states ORDER BY created_at DESC"  # noqa: S608
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (int(limit), max(0, int(offset)))
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list oauth_states"
            logger.warning(
                PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_state(row) for row in rows)
        except (ValueError, TypeError) as exc:
            msg = "Failed to deserialize oauth_state rows"
            logger.warning(
                PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def mark_consumed(
        self,
        state_token: NotBlankStr,
        *,
        connection_name: NotBlankStr,
        consumed_at: datetime,
    ) -> bool:
        """Stamp ``consumed_at`` and ``connection_name_returned`` atomically.

        Compare-and-set: only updates a row whose ``consumed_at IS
        NULL``. A redelivered callback observes the existing
        ``consumed_at`` and returns ``False`` so the handler can route
        it through the replay branch.

        Returns:
            ``True`` when this call stamped the row, ``False`` when the row had already
            been consumed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "UPDATE oauth_states "
                    "SET consumed_at = ?, connection_name_returned = ? "
                    "WHERE state_token = ? AND consumed_at IS NULL",
                    (
                        format_iso_utc(consumed_at),
                        str(connection_name),
                        str(state_token),
                    ),
                ) as cursor:
                    updated = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to mark consumed oauth_state {state_token!r}"
                logger.warning(
                    PERSISTENCE_OAUTH_STATE_SAVE_FAILED,
                    state_token=str(state_token),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return updated

    async def cleanup_expired(self, retention_seconds: float) -> int:
        """Delete expired and stale-consumed OAuth states.

        Reaps two row classes:
        * ``expires_at <= now`` -- the in-flight window has elapsed.
        * ``consumed_at IS NOT NULL AND consumed_at <= now -
          retention_seconds`` -- the row is past the idempotent-replay
          retention window.

        The retention budget is resolved from the
        ``integrations.oauth_idempotency_retention_seconds`` setting
        by the lifecycle cleanup loop and passed in here.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        now = datetime.now(UTC)
        cutoff_iso = format_iso_utc(now)
        consumed_cutoff_iso = format_iso_utc(
            now - timedelta(seconds=retention_seconds),
        )
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM oauth_states "
                    "WHERE expires_at <= ? "
                    "OR (consumed_at IS NOT NULL AND consumed_at <= ?)",
                    (cutoff_iso, consumed_cutoff_iso),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to cleanup expired oauth_states"
                logger.warning(
                    PERSISTENCE_OAUTH_STATE_CLEANUP_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if removed:
            logger.info(PERSISTENCE_OAUTH_STATE_CLEANUP, removed=removed)
        return removed
