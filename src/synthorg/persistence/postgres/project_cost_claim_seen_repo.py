"""Postgres repository for durable project-cost-claim dedup.

Postgres sibling of
:mod:`synthorg.persistence.sqlite.project_cost_claim_seen_repo`.
``seen_at`` and ``expires_at`` are ``TIMESTAMPTZ`` columns instead of
TEXT; ``mark_seen`` uses the same ``INSERT ... ON CONFLICT DO NOTHING``
pattern and ``cursor.rowcount`` distinguishes first-write from
duplicate. ``has_seen`` is the pre-increment existence check.
"""

import contextlib
from datetime import datetime, timedelta

import psycopg
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.project_cost_claim_seen import (
    PERSISTENCE_COST_CLAIM_SEEN_LOOKUP_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_MARK_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_PRUNE_FAILED,
    PERSISTENCE_COST_CLAIM_SEEN_PRUNED,
)
from synthorg.persistence._shared import normalize_utc

logger = get_logger(__name__)


class PostgresProjectCostClaimSeenRepository:
    """Postgres implementation of :class:`ProjectCostClaimSeenRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def has_seen(
        self,
        *,
        claim_id: NotBlankStr,
    ) -> bool:
        """Return ``True`` when a row for ``claim_id`` exists.

        Returns:
            ``True`` when a row for ``claim_id`` exists, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM project_cost_claim_seen WHERE claim_id = %s LIMIT 1",
                    (str(claim_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to look up cost claim {claim_id!r}"
            logger.warning(
                PERSISTENCE_COST_CLAIM_SEEN_LOOKUP_FAILED,
                claim_id=str(claim_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return row is not None

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: datetime,
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
                    INSERT INTO project_cost_claim_seen (
                        claim_id, project_id, seen_at, expires_at
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT(claim_id) DO NOTHING
                    """,
                    (
                        str(claim_id),
                        str(project_id),
                        seen_at,
                        expires_at,
                    ),
                )
                inserted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to mark cost claim {claim_id!r} as seen"
            logger.warning(
                PERSISTENCE_COST_CLAIM_SEEN_MARK_FAILED,
                claim_id=str(claim_id),
                project_id=str(project_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return inserted

    async def prune_expired(self, now: datetime) -> int:
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
                        "DELETE FROM project_cost_claim_seen WHERE expires_at < %s",
                        (cutoff,),
                    )
                    removed = cur.rowcount
                    await conn.commit()
                except psycopg.Error:
                    with contextlib.suppress(psycopg.Error):
                        await conn.rollback()
                    raise
        except psycopg.Error as exc:
            msg = "Failed to prune expired project_cost_claim_seen rows"
            logger.warning(
                PERSISTENCE_COST_CLAIM_SEEN_PRUNE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_COST_CLAIM_SEEN_PRUNED,
                removed=removed,
            )
        return removed
