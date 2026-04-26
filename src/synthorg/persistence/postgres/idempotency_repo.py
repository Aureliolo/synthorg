"""Postgres-backed idempotency-key repository (#1599).

The atomic primitive uses ``INSERT ... ON CONFLICT DO NOTHING
RETURNING`` on the in-flight insert path; if the conflict fires we
re-fetch the existing row to discriminate ``IN_FLIGHT`` from
``COMPLETED`` and to overwrite expired/failed rows in a follow-up
``UPDATE``. Postgres transaction isolation makes the pair safe
without a process-wide lock.
"""

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

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
        """Atomically claim ``(scope, key)`` for *ttl_seconds*."""
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor(
                    row_factory=self._dict_row,
                ) as cur,
            ):
                await cur.execute(
                    "SELECT status, response_body, expires_at "
                    "FROM idempotency_keys "
                    "WHERE scope = %s AND key = %s FOR UPDATE",
                    (scope, key),
                )
                row = await cur.fetchone()
                if row is not None:
                    status = row["status"]
                    row_expires = row["expires_at"]
                    if row_expires > now and status == "completed":
                        cached = row["response_body"]
                        cached_str = json.dumps(cached) if cached is not None else None
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.COMPLETED,
                            cached_response=cached_str,
                        )
                    if row_expires > now and status == "in_flight":
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.IN_FLIGHT,
                        )
                    await cur.execute(
                        "UPDATE idempotency_keys "
                        "SET status = 'in_flight', response_hash = NULL, "
                        "response_body = NULL, "
                        "created_at = %s, expires_at = %s "
                        "WHERE scope = %s AND key = %s",
                        (now, expires_at, scope, key),
                    )
                else:
                    await cur.execute(
                        "INSERT INTO idempotency_keys "
                        "(scope, key, status, created_at, expires_at) "
                        "VALUES (%s, %s, 'in_flight', %s, %s)",
                        (scope, key, now, expires_at),
                    )
            return IdempotencyClaim(outcome=IdempotencyOutcome.FRESH)
        except Exception as exc:
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
        """Mark a claimed key as ``COMPLETED`` and persist the response."""
        # response_body arrives as a serialized JSON string; cast back
        # so Postgres stores it as JSONB.
        try:
            payload = json.loads(response_body)
        except (json.JSONDecodeError, TypeError) as exc:
            msg = "response_body must be JSON-serializable"
            raise QueryError(msg) from exc
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'completed', response_body = %s::jsonb, "
                    "response_hash = %s "
                    "WHERE scope = %s AND key = %s",
                    (json.dumps(payload), response_hash, scope, key),
                )
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="cleanup_expired",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to cleanup expired idempotency keys"
            raise QueryError(msg) from exc
