"""Postgres-backed idempotency-key repository (#1599).

The atomic claim primitive is ``INSERT ... ON CONFLICT DO NOTHING
RETURNING`` -- the unique-PK constraint serialises competing FRESH
attempts at the database level rather than relying on
``SELECT FOR UPDATE`` (which doesn't lock non-existent rows under
``READ COMMITTED``). On conflict we re-fetch the existing row under
``FOR UPDATE`` to discriminate ``IN_FLIGHT`` from ``COMPLETED`` and
to overwrite expired/failed rows in a follow-up ``UPDATE``.
"""

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from psycopg import Error as PsycopgError

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.idempotency import (
    IDEMPOTENCY_PERSISTENCE_ERROR,
)
from synthorg.persistence.errors import QueryError
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``."""
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


class PostgresIdempotencyRepository:
    """Postgres implementation of :class:`IdempotencyRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dict_row = _import_dict_row()

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: datetime,
    ) -> IdempotencyClaim:
        """Atomically claim ``(scope, key)`` for *ttl_seconds*.

        Step 1 attempts ``INSERT ... ON CONFLICT DO NOTHING RETURNING``;
        a returned row means we won the FRESH race. On conflict, step 2
        re-fetches the existing row under ``FOR UPDATE`` and routes:
        completed → cached response, in-flight → IN_FLIGHT, expired or
        failed → overwrite back to in-flight and return FRESH.
        """
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor(row_factory=self._dict_row) as cur,
            ):
                # Step 1: claim by INSERT. Postgres' unique PK guarantees
                # exactly one of N concurrent callers wins.
                await cur.execute(
                    "INSERT INTO idempotency_keys "
                    "(scope, key, status, created_at, expires_at) "
                    "VALUES (%s, %s, 'in_flight', %s, %s) "
                    "ON CONFLICT (scope, key) DO NOTHING "
                    "RETURNING scope",
                    (scope, key, now, expires_at),
                )
                if await cur.fetchone() is not None:
                    return IdempotencyClaim(outcome=IdempotencyOutcome.FRESH)

                # Step 2: ON CONFLICT fired. Lock and inspect the row.
                await cur.execute(
                    "SELECT status, response_body, expires_at "
                    "FROM idempotency_keys "
                    "WHERE scope = %s AND key = %s FOR UPDATE",
                    (scope, key),
                )
                row = await cur.fetchone()
                if row is None:
                    # Vanishingly rare race -- the row was deleted between
                    # our conflict and the SELECT. Treat as FRESH and let
                    # the caller proceed; if a second concurrent FRESH
                    # attempts an INSERT it will re-conflict.
                    return IdempotencyClaim(outcome=IdempotencyOutcome.FRESH)
                status = row["status"]
                row_expires = row["expires_at"]
                if row_expires > now and status == "completed":
                    cached = row["response_body"]
                    # Postgres JSONB is auto-deserialised; re-serialise
                    # to the protocol's ``str`` shape (matches SQLite's
                    # TEXT-stored response_body so callers see one type).
                    cached_str = json.dumps(cached) if cached is not None else None
                    return IdempotencyClaim(
                        outcome=IdempotencyOutcome.COMPLETED,
                        cached_response=cached_str,
                    )
                if row_expires > now and status == "in_flight":
                    return IdempotencyClaim(
                        outcome=IdempotencyOutcome.IN_FLIGHT,
                    )
                # Expired or failed -- overwrite as a fresh claim.
                await cur.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'in_flight', response_hash = NULL, "
                    "response_body = NULL, "
                    "created_at = %s, expires_at = %s "
                    "WHERE scope = %s AND key = %s",
                    (now, expires_at, scope, key),
                )
                return IdempotencyClaim(outcome=IdempotencyOutcome.FRESH)
        except PsycopgError as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="claim",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to claim idempotency key"
            raise QueryError(msg) from exc

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
    ) -> None:
        """Mark a claimed key as ``COMPLETED`` and persist the response.

        ``response_body`` is the canonical JSON string produced by the
        service layer; it goes straight into the JSONB column via
        ``%s::jsonb`` -- no ``json.loads`` round-trip.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'completed', response_body = %s::jsonb, "
                    "response_hash = %s "
                    "WHERE scope = %s AND key = %s",
                    (response_body, response_hash, scope, key),
                )
        except PsycopgError as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="complete",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to record idempotency completion"
            raise QueryError(msg) from exc

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> None:
        """Mark a claimed key as ``FAILED`` so future retries can re-claim."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE idempotency_keys SET status = 'failed' "
                    "WHERE scope = %s AND key = %s",
                    (scope, key),
                )
        except PsycopgError as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="fail",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to record idempotency failure"
            raise QueryError(msg) from exc

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        """Fetch the persisted record verbatim, or None when absent."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(
                    row_factory=self._dict_row,
                ) as cur,
            ):
                await cur.execute(
                    "SELECT scope, key, status, response_hash, "
                    "response_body, created_at, expires_at "
                    "FROM idempotency_keys "
                    "WHERE scope = %s AND key = %s",
                    (scope, key),
                )
                row = await cur.fetchone()
        except PsycopgError as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="get",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to fetch idempotency key"
            raise QueryError(msg) from exc

        if row is None:
            return None
        cached = row["response_body"]
        cached_str = json.dumps(cached) if cached is not None else None
        return IdempotencyRecord(
            scope=NotBlankStr(str(row["scope"])),
            key=NotBlankStr(str(row["key"])),
            status=IdempotencyOutcome(row["status"]),
            response_hash=row["response_hash"],
            response_body=cached_str,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    async def cleanup_expired(self, now: datetime) -> int:
        """Delete expired rows and return the count removed."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM idempotency_keys WHERE expires_at <= %s",
                    (now,),
                )
                return int(cur.rowcount or 0)
        except PsycopgError as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="cleanup_expired",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to cleanup expired idempotency keys"
            raise QueryError(msg) from exc
