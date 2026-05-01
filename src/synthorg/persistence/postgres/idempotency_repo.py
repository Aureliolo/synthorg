"""Postgres-backed idempotency-key repository.

The atomic claim primitive is ``INSERT ... ON CONFLICT DO NOTHING
RETURNING`` -- the unique-PK constraint serialises competing FRESH
attempts at the database level rather than relying on
``SELECT FOR UPDATE`` (which doesn't lock non-existent rows under
``READ COMMITTED``). On conflict we re-fetch the existing row under
``FOR UPDATE`` to discriminate ``IN_FLIGHT`` from ``COMPLETED`` and
to overwrite expired/failed rows in a follow-up ``UPDATE``.
"""

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from psycopg import Error as PsycopgError
from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.idempotency import (
    IDEMPOTENCY_PERSISTENCE_ERROR,
)
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
                    if await self._attempt_insert(
                        cur,
                        scope=scope,
                        key=key,
                        new_token=new_token,
                        now=now,
                        expires_at=expires_at,
                    ):
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.FRESH,
                            claim_token=NotBlankStr(new_token),
                        )

                    classified = await self._select_for_update_and_classify(
                        cur,
                        scope=scope,
                        key=key,
                        now=now,
                    )
                    if classified is None:
                        # Row vanished between our conflict and the
                        # SELECT (concurrent cleanup). Retry the whole
                        # protocol.
                        last_status = "row_vanished"
                        continue
                    outcome, cached_str = classified
                    if outcome is IdempotencyOutcome.COMPLETED:
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.COMPLETED,
                            cached_response=cached_str,
                        )
                    if outcome is IdempotencyOutcome.IN_FLIGHT:
                        return IdempotencyClaim(
                            outcome=IdempotencyOutcome.IN_FLIGHT,
                        )
                    # Expired or failed -- rotate the lease.
                    await self._reclaim_update(
                        cur,
                        scope=scope,
                        key=key,
                        new_token=new_token,
                        expires_at=expires_at,
                    )
                    return IdempotencyClaim(
                        outcome=IdempotencyOutcome.FRESH,
                        claim_token=NotBlankStr(new_token),
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

    async def _attempt_insert(  # noqa: PLR0913
        self,
        cur: Any,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        new_token: str,
        now: AwareDatetime,
        expires_at: AwareDatetime,
    ) -> bool:
        """Try to win FRESH via ``INSERT ... ON CONFLICT DO NOTHING``.

        Returns ``True`` when the insert landed (we own the lease);
        ``False`` on conflict (the caller must SELECT FOR UPDATE to
        decide what to do with the existing row).
        """
        await cur.execute(
            "INSERT INTO idempotency_keys "
            "(scope, key, status, claim_token, "
            "created_at, expires_at) "
            "VALUES (%s, %s, 'in_flight', %s, %s, %s) "
            "ON CONFLICT (scope, key) DO NOTHING "
            "RETURNING scope",
            (scope, key, new_token, now, expires_at),
        )
        return await cur.fetchone() is not None

    async def _select_for_update_and_classify(
        self,
        cur: Any,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        now: AwareDatetime,
    ) -> tuple[IdempotencyOutcome, str | None] | None:
        """Lock the existing row and classify the next outcome.

        Returns ``None`` when the row vanished (caller retries the
        whole protocol). Otherwise returns ``(outcome, cached_str)``:
        - ``COMPLETED`` + non-None ``cached_str``
        - ``IN_FLIGHT`` + ``None``
        - ``FRESH`` + ``None`` (caller must run the reclaim UPDATE)
        """
        await cur.execute(
            "SELECT status, response_body, expires_at "
            "FROM idempotency_keys "
            "WHERE scope = %s AND key = %s FOR UPDATE",
            (scope, key),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        status = row["status"]
        row_expires = row["expires_at"]
        if row_expires > now and status == "completed":
            # ``response_body`` is TEXT (not JSONB) so the verbatim
            # bytes round-trip back to the caller -- matching the
            # SQLite backend and preserving the ``response_hash``
            # contract (a JSONB column would canonicalise key order
            # and whitespace, breaking re-hash equality).
            cached_str = row["response_body"]
            return (IdempotencyOutcome.COMPLETED, cached_str)
        if row_expires > now and status == "in_flight":
            return (IdempotencyOutcome.IN_FLIGHT, None)
        # Expired OR failed -- caller will reclaim with a fresh lease.
        return (IdempotencyOutcome.FRESH, None)

    async def _reclaim_update(
        self,
        cur: Any,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        new_token: str,
        expires_at: AwareDatetime,
    ) -> None:
        """Rotate an expired/failed row to a fresh in-flight lease.

        ``created_at`` is intentionally NOT in the SET clause so
        ``IdempotencyRecord.created_at`` keeps its original-insertion
        semantics across re-claims (per the protocol contract).
        """
        await cur.execute(
            "UPDATE idempotency_keys "
            "SET status = 'in_flight', claim_token = %s, "
            "response_hash = NULL, response_body = NULL, "
            "expires_at = %s "
            "WHERE scope = %s AND key = %s",
            (new_token, expires_at, scope, key),
        )

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
        claim_token: NotBlankStr,
    ) -> bool:
        """Mark a claimed key as ``COMPLETED`` if *claim_token* matches.

        ``response_body`` is the canonical JSON string produced by the
        service layer; it goes straight into the TEXT column verbatim
        so the bytes round-trip back to the caller (preserves the
        ``response_hash`` integrity contract and stays in lockstep
        with the SQLite backend). Returns ``True`` when the row's
        stored token matched and the UPDATE landed; ``False`` when
        the lease has rotated.
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
                    "SET status = 'completed', response_body = %s, "
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
        claim_token: NotBlankStr,
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
        try:
            status = IdempotencyOutcome(row["status"])
            # ``response_body`` is TEXT (not JSONB) so the bytes flow
            # through verbatim -- matches SQLite and preserves the
            # ``response_hash`` round-trip. Pass through as-is; the
            # ``IdempotencyRecord`` validator below catches schema
            # violations (response_body NULL/non-NULL must agree
            # with status).
            return IdempotencyRecord(
                scope=NotBlankStr(str(row["scope"])),
                key=NotBlankStr(str(row["key"])),
                status=status,
                response_hash=row["response_hash"],
                response_body=row["response_body"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
        except (ValueError, TypeError) as exc:
            # Decode / validation failure on a corrupt row. Routing
            # through the same QueryError contract as the SQL path so
            # callers do not have to handle a second exception type.
            logger.warning(
                IDEMPOTENCY_PERSISTENCE_ERROR,
                operation="get",
                scope=scope,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to fetch idempotency key"
            raise QueryError(msg) from exc

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
