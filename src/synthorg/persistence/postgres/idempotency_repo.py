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
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from psycopg import Error as PsycopgError
from pydantic import AwareDatetime  # noqa: TC002

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

# Bound retries on the INSERT/FOR-UPDATE race-window when a concurrent
# cleanup deletes the row after our INSERT conflict but before the
# SELECT. Three attempts are enough for any realistic scenario; an
# attacker cannot drive this loop because the row deletion requires
# either ``cleanup_expired`` or another caller succeeding -- both of
# which converge to a stable claim within a couple of iterations.
_CLAIM_INSERT_RETRIES: int = 3


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
        now: AwareDatetime,
    ) -> IdempotencyClaim:
        """Atomically claim ``(scope, key)`` for *ttl_seconds*.

        Each attempt runs the same two-step protocol:

        1. ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` -- a returned
           row means we won the FRESH race.
        2. On conflict, re-fetch the existing row under ``FOR UPDATE``
           and route: completed → cached response, in-flight →
           IN_FLIGHT, expired or failed → overwrite to in-flight and
           return FRESH.

        If step 2's SELECT returns no row -- a concurrent
        ``cleanup_expired`` removed it after our INSERT-conflict but
        before the lock -- we *retry the whole protocol* up to
        ``_CLAIM_INSERT_RETRIES`` times. Returning FRESH without a
        durable row would let ``complete``/``fail`` UPDATE zero rows
        and silently lose the cached response.
        """
        expires_at = now + timedelta(seconds=ttl_seconds)
        last_status: str | None = None
        try:
            for _ in range(_CLAIM_INSERT_RETRIES):
                async with (
                    self._pool.connection() as conn,
                    conn.transaction(),
                    conn.cursor(row_factory=self._dict_row) as cur,
                ):
                    new_token = secrets.token_hex(16)
                    await cur.execute(
                        "INSERT INTO idempotency_keys "
                        "(scope, key, status, claim_token, "
                        "created_at, expires_at) "
                        "VALUES (%s, %s, 'in_flight', %s, %s, %s) "
                        "ON CONFLICT (scope, key) DO NOTHING "
                        "RETURNING scope",
                        (scope, key, new_token, now, expires_at),
                    )
                    if await cur.fetchone() is not None:
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.FRESH,
                            claim_token=new_token,
                        )

                    await cur.execute(
                        "SELECT status, response_body, expires_at "
                        "FROM idempotency_keys "
                        "WHERE scope = %s AND key = %s FOR UPDATE",
                        (scope, key),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        # Row vanished between our conflict and the
                        # SELECT (concurrent cleanup). Retry the whole
                        # protocol; the next INSERT either wins FRESH
                        # or re-conflicts against a sibling that just
                        # re-inserted.
                        last_status = "row_vanished"
                        continue

                    status = row["status"]
                    row_expires = row["expires_at"]
                    if row_expires > now and status == "completed":
                        cached = row["response_body"]
                        # Always serialise: a stored JSONB ``null``
                        # round-trips through psycopg as Python None
                        # but is a *legitimate cached null response*,
                        # not a missing body. Conditional ``None``
                        # passthrough would lose that distinction and
                        # also violate the IdempotencyClaim invariant
                        # (COMPLETED requires non-None cached_response).
                        # ``json.dumps(None)`` -> ``"null"``.
                        cached_str = json.dumps(cached)
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.COMPLETED,
                            cached_response=cached_str,
                        )
                    if row_expires > now and status == "in_flight":
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.IN_FLIGHT,
                        )
                    # Expired or failed -- rotate the lease token so a
                    # stale worker holding the old one cannot CAS
                    # against the new claim.
                    await cur.execute(
                        "UPDATE idempotency_keys "
                        "SET status = 'in_flight', claim_token = %s, "
                        "response_hash = NULL, response_body = NULL, "
                        "created_at = %s, expires_at = %s "
                        "WHERE scope = %s AND key = %s",
                        (new_token, now, expires_at, scope, key),
                    )
                    return IdempotencyClaim(
                        outcome=IdempotencyOutcome.FRESH,
                        claim_token=new_token,
                    )
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

        logger.warning(
            IDEMPOTENCY_PERSISTENCE_ERROR,
            operation="claim",
            scope=scope,
            note="claim_retry_exhausted",
            last_status=last_status,
            retries=_CLAIM_INSERT_RETRIES,
        )
        msg = (
            "Failed to claim idempotency key after "
            f"{_CLAIM_INSERT_RETRIES} insert/select retries"
        )
        raise QueryError(msg)

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
        claim_token: str,
    ) -> bool:
        """Mark a claimed key as ``COMPLETED`` if *claim_token* matches.

        ``response_body`` is the canonical JSON string produced by the
        service layer; it goes straight into the JSONB column via
        ``%s::jsonb`` -- no ``json.loads`` round-trip. Returns
        ``True`` when the row's stored token matched and the UPDATE
        landed; ``False`` when the lease has rotated.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                # Gate on status = 'in_flight' so a stale worker cannot
                # flip an already-completed row -- completed rows MUST
                # be immutable for the lifetime of the lease, otherwise
                # a slow callback whose lease was rotated and re-claimed
                # could overwrite the new lease's cached response even
                # if the token CAS coincidentally matched (e.g. token
                # rotation happens to re-issue the same value, however
                # unlikely with 16 random bytes).
                await cur.execute(
                    "UPDATE idempotency_keys "
                    "SET status = 'completed', response_body = %s::jsonb, "
                    "response_hash = %s "
                    "WHERE scope = %s AND key = %s "
                    "AND claim_token = %s "
                    "AND status = 'in_flight'",
                    (response_body, response_hash, scope, key, claim_token),
                )
                return cur.rowcount > 0
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
        claim_token: str,
    ) -> bool:
        """Mark a claimed key as ``FAILED`` if *claim_token* matches.

        Same status-gate as :meth:`complete`: only an in-flight row
        with the matching lease token can transition to failed.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE idempotency_keys SET status = 'failed' "
                    "WHERE scope = %s AND key = %s "
                    "AND claim_token = %s "
                    "AND status = 'in_flight'",
                    (scope, key, claim_token),
                )
                return cur.rowcount > 0
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
        status = IdempotencyOutcome(row["status"])
        # When status is COMPLETED, ``response_body`` is a JSONB column
        # we MUST surface as a JSON string (a stored JSONB ``null``
        # round-trips as Python None but is a legitimate cached null
        # response). For non-completed rows the schema CHECK forces
        # ``response_body`` to SQL NULL and the IdempotencyRecord
        # invariant requires Python None -- pass it through verbatim.
        cached = row["response_body"]
        if status is IdempotencyOutcome.COMPLETED:
            cached_str: str | None = json.dumps(cached)
        else:
            cached_str = None
        return IdempotencyRecord(
            scope=NotBlankStr(str(row["scope"])),
            key=NotBlankStr(str(row["key"])),
            status=status,
            response_hash=row["response_hash"],
            response_body=cached_str,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    async def cleanup_expired(self, now: AwareDatetime) -> int:
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
