"""Postgres-backed OAuth authorization-state repository.

Persists transient :class:`OAuthState` rows in the ``oauth_states``
table.  States are short-lived and consumed once on callback;
``cleanup_expired`` reclaims stale rows.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

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
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


_SELECT_COLS = (
    "state_token, connection_name, pkce_verifier, "
    "scopes_requested, redirect_uri, created_at, expires_at"
)


def _row_to_state(row: dict[str, Any]) -> OAuthState:
    """Deserialize a dict row into an :class:`OAuthState`."""
    pkce = row.get("pkce_verifier")
    return OAuthState(
        state_token=NotBlankStr(row["state_token"]),
        connection_name=NotBlankStr(row["connection_name"]),
        pkce_verifier=NotBlankStr(pkce) if pkce else None,
        scopes_requested=row.get("scopes_requested") or "",
        redirect_uri=row.get("redirect_uri") or "",
        created_at=coerce_row_timestamp(row["created_at"]),
        expires_at=coerce_row_timestamp(row["expires_at"]),
    )


class PostgresOAuthStateRepository:
    """Postgres implementation of :class:`OAuthStateRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def save(self, state: OAuthState) -> None:
        """Upsert an OAuth state row keyed by ``state_token``."""
        params = (
            str(state.state_token),
            str(state.connection_name),
            str(state.pkce_verifier) if state.pkce_verifier else None,
            state.scopes_requested,
            state.redirect_uri,
            normalize_utc(state.created_at),
            normalize_utc(state.expires_at),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO oauth_states (
                        state_token, connection_name, pkce_verifier,
                        scopes_requested, redirect_uri, created_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (state_token) DO UPDATE SET
                        connection_name = EXCLUDED.connection_name,
                        pkce_verifier = EXCLUDED.pkce_verifier,
                        scopes_requested = EXCLUDED.scopes_requested,
                        redirect_uri = EXCLUDED.redirect_uri,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    params,
                )
        except Exception as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM oauth_states "  # noqa: S608
                    "WHERE state_token = %s",
                    (str(state_token),),
                )
                row = await cur.fetchone()
        except Exception as exc:
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
        """Delete an OAuth state; return ``True`` if a row was removed."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM oauth_states WHERE state_token = %s",
                    (str(state_token),),
                )
                deleted = cur.rowcount > 0
        except Exception as exc:
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
        cutoff = datetime.now(UTC)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM oauth_states WHERE expires_at <= %s",
                    (cutoff,),
                )
                removed = cur.rowcount
        except Exception as exc:
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
