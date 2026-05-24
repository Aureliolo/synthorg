"""Postgres implementation of the CircuitBreakerStateRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/circuit_breaker_repo.py.
Postgres stores opened_at as DOUBLE PRECISION (Unix float timestamp) and
bounce/trip counts as BIGINT.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CIRCUIT_BREAKER_DELETE_FAILED,
    PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
    PERSISTENCE_CIRCUIT_BREAKER_LOADED,
    PERSISTENCE_CIRCUIT_BREAKER_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.circuit_breaker_protocol import (
    CircuitBreakerPairKey,
    CircuitBreakerStateRecord,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresCircuitBreakerStateRepository:
    """Postgres implementation of the CircuitBreakerStateRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: CircuitBreakerStateRecord) -> None:
        """Persist a circuit breaker state record (upsert by pair key).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO circuit_breaker_state (
    pair_key_a, pair_key_b, bounce_count, trip_count, opened_at
) VALUES (
    %(pair_key_a)s, %(pair_key_b)s, %(bounce_count)s, %(trip_count)s, %(opened_at)s
)
ON CONFLICT(pair_key_a, pair_key_b) DO UPDATE SET
    bounce_count=EXCLUDED.bounce_count,
    trip_count=EXCLUDED.trip_count,
    opened_at=EXCLUDED.opened_at""",
                    record.model_dump(mode="json"),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to save circuit breaker state for pair "
                f"({record.pair_key_a!r}, {record.pair_key_b!r})"
            )
            logger.warning(
                PERSISTENCE_CIRCUIT_BREAKER_SAVE_FAILED,
                pair_key_a=record.pair_key_a,
                pair_key_b=record.pair_key_b,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        entity_id: CircuitBreakerPairKey,
    ) -> CircuitBreakerStateRecord | None:
        """Retrieve one circuit breaker state record by composite key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        pair_key_a, pair_key_b = entity_id
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT pair_key_a, pair_key_b, bounce_count, "
                    "trip_count, opened_at FROM circuit_breaker_state "
                    "WHERE pair_key_a = %s AND pair_key_b = %s",
                    (pair_key_a, pair_key_b),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = (
                f"Failed to fetch circuit breaker state for pair "
                f"({pair_key_a!r}, {pair_key_b!r})"
            )
            logger.warning(
                PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
                pair_key_a=pair_key_a,
                pair_key_b=pair_key_b,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None
        try:
            return CircuitBreakerStateRecord.model_validate(row)
        except ValidationError as exc:
            msg = (
                f"Failed to deserialize circuit breaker state row "
                f"({pair_key_a!r}, {pair_key_b!r})"
            )
            logger.warning(
                PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
                pair_key_a=pair_key_a,
                pair_key_b=pair_key_b,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="deserialization failed",
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        """List records ordered by ``(pair_key_a, pair_key_b)`` ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT pair_key_a, pair_key_b, bounce_count, "
                    "trip_count, opened_at FROM circuit_breaker_state "
                    "ORDER BY pair_key_a, pair_key_b LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list circuit breaker state"
            logger.warning(
                PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[CircuitBreakerStateRecord] = []
        for row in rows:
            try:
                results.append(CircuitBreakerStateRecord.model_validate(row))
            except ValidationError as exc:
                msg = (
                    f"Failed to deserialize circuit breaker state row "
                    f"({row.get('pair_key_a') if row else 'unknown'})"
                )
                logger.warning(
                    PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
                    pair_key_a=row.get("pair_key_a") if row else "unknown",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="deserialization failed",
                )
                raise QueryError(msg) from exc

        logger.debug(PERSISTENCE_CIRCUIT_BREAKER_LOADED, count=len(results))
        return tuple(results)

    async def load_all(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        """Load a bounded page of records in ``(pair_key_a, pair_key_b)``.

        Delegates to :meth:`list_items` (same deterministic key order
        and pagination contract); kept as a distinct ADR-0001 D7
        method because boot-time callers drain it via
        :func:`synthorg.persistence._shared.collect_all`.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        return await self.list_items(limit=limit, offset=offset)

    async def delete(self, entity_id: CircuitBreakerPairKey) -> bool:
        """Delete a circuit breaker state record by composite key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        pair_key_a, pair_key_b = entity_id
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM circuit_breaker_state "
                    "WHERE pair_key_a = %s AND pair_key_b = %s",
                    (pair_key_a, pair_key_b),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to delete circuit breaker state for pair "
                f"({pair_key_a!r}, {pair_key_b!r})"
            )
            logger.warning(
                PERSISTENCE_CIRCUIT_BREAKER_DELETE_FAILED,
                pair_key_a=pair_key_a,
                pair_key_b=pair_key_b,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted
