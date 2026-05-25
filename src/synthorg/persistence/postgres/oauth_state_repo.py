"""Postgres-backed OAuth authorization-state repository.

Persists transient :class:`OAuthState` rows in the ``oauth_states``
table.  States are short-lived and consumed once on callback;
``cleanup_expired`` reclaims stale rows.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from synthorg.core.persistence_errors import QueryError
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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    normalize_utc,
    validate_pagination_args,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)

_SELECT_COLS = (
    "state_token, connection_name, pkce_verifier, nonce, "
    "scopes_requested, redirect_uri, created_at, expires_at, "
    "consumed_at, connection_name_returned"
)


def _row_to_state(row: dict[str, Any]) -> OAuthState:
    """Deserialize a dict row into an :class:`OAuthState`.

    Returns:
        Result of type ``OAuthState``.
    """
    pkce = row.get("pkce_verifier")
    nonce = row.get("nonce")
    consumed_at = row.get("consumed_at")
    connection_name_returned = row.get("connection_name_returned")
    return OAuthState(
        state_token=NotBlankStr(row["state_token"]),
        connection_name=NotBlankStr(row["connection_name"]),
        pkce_verifier=NotBlankStr(pkce) if pkce else None,
        nonce=NotBlankStr(nonce) if nonce else None,
        scopes_requested=row.get("scopes_requested") or "",
        redirect_uri=row.get("redirect_uri") or "",
        created_at=coerce_row_timestamp(row["created_at"]),
        expires_at=coerce_row_timestamp(row["expires_at"]),
        consumed_at=(
            coerce_row_timestamp(consumed_at) if consumed_at is not None else None
        ),
        connection_name_returned=(
            NotBlankStr(connection_name_returned) if connection_name_returned else None
        ),
    )


class PostgresOAuthStateRepository:
    """Postgres implementation of :class:`OAuthStateRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def save(self, state: OAuthState) -> None:
        """Upsert an OAuth state row keyed by ``state_token``.

        Persists every column on ``OAuthState`` including
        ``consumed_at`` / ``connection_name_returned`` so the model
        and the row stay symmetric across save/load. The two
        idempotency columns are typically ``None`` at flow-start
        (``OAuthStateService.persist_initiation``) and stamped
        later by ``mark_consumed``; saving them here lets tests
        construct a "post-callback" snapshot in a single round-trip.

        Raises:
            QueryError: If the database query fails.
        """
        params = (
            str(state.state_token),
            str(state.connection_name),
            str(state.pkce_verifier) if state.pkce_verifier else None,
            str(state.nonce) if state.nonce else None,
            state.scopes_requested,
            state.redirect_uri,
            normalize_utc(state.created_at),
            normalize_utc(state.expires_at),
            normalize_utc(state.consumed_at) if state.consumed_at else None,
            (
                str(state.connection_name_returned)
                if state.connection_name_returned
                else None
            ),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO oauth_states (
                        state_token, connection_name, pkce_verifier, nonce,
                        scopes_requested, redirect_uri, created_at, expires_at,
                        consumed_at, connection_name_returned
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (state_token) DO UPDATE SET
                        connection_name = EXCLUDED.connection_name,
                        pkce_verifier = EXCLUDED.pkce_verifier,
                        nonce = EXCLUDED.nonce,
                        scopes_requested = EXCLUDED.scopes_requested,
                        redirect_uri = EXCLUDED.redirect_uri,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at,
                        consumed_at = COALESCE(
                            oauth_states.consumed_at, EXCLUDED.consumed_at
                        ),
                        connection_name_returned = COALESCE(
                            oauth_states.connection_name_returned,
                            EXCLUDED.connection_name_returned
                        )
                    """,
                    params,
                )
                await conn.commit()
        except psycopg.Error as exc:
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
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM oauth_states WHERE state_token = %s",
                    (str(state_token),),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
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
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_OAUTH_STATE_FETCH_FAILED
        )
        # ``created_at`` is non-unique; ``state_token`` (PK) is the
        # deterministic tie-breaker so offset paging cannot skip or
        # duplicate rows that share a timestamp.
        sql = (
            f"SELECT {_SELECT_COLS} FROM oauth_states "  # noqa: S608
            "ORDER BY created_at DESC, state_token ASC"
        )
        sql += " LIMIT %s OFFSET %s"
        params: tuple[object, ...] = (limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list oauth_states"
            logger.warning(
                PERSISTENCE_OAUTH_STATE_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_state(row) for row in rows)
        except (ValueError, TypeError, KeyError) as exc:
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
        ``consumed_at`` and returns ``False`` so the handler can
        route it through the replay branch.

        Returns:
            ``True`` when this call stamped the row, ``False`` when the row had already
            been consumed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE oauth_states "
                    "SET consumed_at = %s, connection_name_returned = %s "
                    "WHERE state_token = %s AND consumed_at IS NULL",
                    (
                        normalize_utc(consumed_at),
                        str(connection_name),
                        str(state_token),
                    ),
                )
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
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

        See :meth:`SQLiteOAuthStateRepository.cleanup_expired` for
        the retention contract.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        now = datetime.now(UTC)
        consumed_cutoff = now - timedelta(seconds=retention_seconds)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM oauth_states "
                    "WHERE expires_at <= %s "
                    "OR (consumed_at IS NOT NULL AND consumed_at <= %s)",
                    (now, consumed_cutoff),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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
