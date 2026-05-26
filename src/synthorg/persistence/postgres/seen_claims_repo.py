"""Postgres repository for worker claim dedup persistence.

Postgres sibling of :mod:`synthorg.persistence.sqlite.seen_claims_repo`.
``seen_at`` and ``expires_at`` are ``TIMESTAMPTZ`` columns instead of
TEXT; ``mark_seen`` uses the same ``INSERT ... ON CONFLICT DO NOTHING``
pattern and ``cursor.rowcount`` distinguishes first-write from
duplicate. ``is_completed`` is the pre-execute existence check.
"""

import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING

import psycopg
from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED,
    PERSISTENCE_SEEN_CLAIMS_MARK_FAILED,
    PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED,
    PERSISTENCE_SEEN_CLAIMS_PRUNED,
)
from synthorg.persistence._shared import normalize_utc

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresSeenClaimsRepository:
    """Postgres implementation of :class:`SeenClaimsRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def is_completed(
        self,
        *,
        idempotency_key: NotBlankStr,
    ) -> bool:
        """Return ``True`` when a row for ``idempotency_key`` exists.

        Returns:
            ``True`` when a row for ``idempotency_key`` exists, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM seen_claims WHERE idempotency_key = %s LIMIT 1",
                    (str(idempotency_key),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to look up seen claim {idempotency_key!r}"
            logger.warning(
                PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED,
                idempotency_key=str(idempotency_key),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return row is not None

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: AwareDatetime,
        ttl_seconds: float,
    ) -> bool:
        """Insert the dedup row; return ``True`` only on first write.

        Returns:
            ``True`` when this call inserted the dedup row, ``False`` when a previous
            call had already inserted it.

        Raises:
            QueryError: If the database query fails.
        """
        seen_at = normalize_utc(now)
        expires_at = seen_at + timedelta(seconds=ttl_seconds)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO seen_claims (
                        idempotency_key, claim_id, seen_at, expires_at
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT(idempotency_key) DO NOTHING
                    """,
                    (
                        str(idempotency_key),
                        str(claim_id),
                        seen_at,
                        expires_at,
                    ),
                )
                inserted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to mark claim {idempotency_key!r} as seen"
            logger.warning(
                PERSISTENCE_SEEN_CLAIMS_MARK_FAILED,
                idempotency_key=str(idempotency_key),
                claim_id=str(claim_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return inserted

    async def prune_expired(self, now: AwareDatetime) -> int:
        """Delete rows past their ``expires_at`` boundary.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """  # noqa: DOC501 -- inner psycopg.Error re-raise is caught by the outer handler and surfaces as QueryError
        cutoff = normalize_utc(now)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                try:
                    await cur.execute(
                        "DELETE FROM seen_claims WHERE expires_at < %s",
                        (cutoff,),
                    )
                    removed = cur.rowcount
                    await conn.commit()
                except psycopg.Error:
                    # ``async with`` will roll back implicitly on
                    # exception, but the explicit ``rollback`` mirrors
                    # the SQLite sibling and avoids leaving the
                    # transaction in an ambiguous state if a future
                    # refactor moves the cursor work outside the
                    # context manager.
                    with contextlib.suppress(psycopg.Error):
                        await conn.rollback()
                    raise
        except psycopg.Error as exc:
            msg = "Failed to prune expired seen_claims rows"
            logger.warning(
                PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_SEEN_CLAIMS_PRUNED,
                removed=removed,
            )
        return removed
