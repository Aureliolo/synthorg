"""SQLite-backed OAuth authorization-state repository.

Persists transient :class:`OAuthState` rows in the ``oauth_states``
table.  States are short-lived (minutes) and consumed once on
callback; ``cleanup_expired`` reclaims stale rows.
"""

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import OAuthState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_OAUTH_STATE_CLEANUP,
    PERSISTENCE_OAUTH_STATE_CLEANUP_FAILED,
    PERSISTENCE_OAUTH_STATE_DELETE_FAILED,
    PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
    PERSISTENCE_OAUTH_STATE_SAVE_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence.errors import QueryError

logger = get_logger(__name__)


_SELECT_COLS = (
    "state_token, connection_name, pkce_verifier, "
    "scopes_requested, redirect_uri, created_at, expires_at"
)


def _row_to_state(row: aiosqlite.Row | tuple[Any, ...]) -> OAuthState:
    """Deserialize a row tuple into an :class:`OAuthState`."""
    (
        state_token,
        connection_name,
        pkce_verifier,
        scopes_requested,
        redirect_uri,
        created_at,
        expires_at,
    ) = row
    return OAuthState(
        state_token=NotBlankStr(state_token),
        connection_name=NotBlankStr(connection_name),
        pkce_verifier=NotBlankStr(pkce_verifier) if pkce_verifier else None,
        scopes_requested=scopes_requested or "",
        redirect_uri=redirect_uri or "",
        created_at=coerce_row_timestamp(created_at),
        expires_at=coerce_row_timestamp(expires_at),
    )


class SQLiteOAuthStateRepository:
    """SQLite implementation of :class:`OAuthStateRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        """Bind to *db* and serialize writes via *write_lock*."""
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, state: OAuthState) -> None:
        """Upsert an OAuth state row keyed by ``state_token``."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    """
                    INSERT INTO oauth_states (
                        state_token, connection_name, pkce_verifier,
                        scopes_requested, redirect_uri, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(state_token) DO UPDATE SET
                        connection_name = excluded.connection_name,
                        pkce_verifier = excluded.pkce_verifier,
                        scopes_requested = excluded.scopes_requested,
                        redirect_uri = excluded.redirect_uri,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        str(state.state_token),
                        str(state.connection_name),
                        str(state.pkce_verifier) if state.pkce_verifier else None,
                        state.scopes_requested,
                        state.redirect_uri,
                        format_iso_utc(state.created_at),
                        format_iso_utc(state.expires_at),
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
        """Fetch an OAuth state by token."""
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
        return _row_to_state(row)

    async def delete(self, state_token: NotBlankStr) -> bool:
        """Delete an OAuth state; return ``True`` if a row was removed."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM oauth_states WHERE state_token = ?",
                    (str(state_token),),
                )
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

    async def cleanup_expired(self) -> int:
        """Delete every state with ``expires_at`` <= now (UTC)."""
        cutoff_iso = format_iso_utc(datetime.now(UTC))
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM oauth_states WHERE expires_at <= ?",
                    (cutoff_iso,),
                )
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
