"""SQLite repository for circuit breaker state persistence."""

import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.critical_errors import reraise_critical
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
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


class SQLiteCircuitBreakerStateRepository:
    """SQLite implementation of the CircuitBreakerStateRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _rollback_quietly(self, event: str) -> None:
        """Roll back the current transaction, swallowing errors."""
        try:
            await self._db.rollback()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                event,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def save(self, record: CircuitBreakerStateRecord) -> None:
        """Persist a circuit breaker state record (upsert by pair key).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT OR REPLACE INTO circuit_breaker_state (
    pair_key_a, pair_key_b, bounce_count, trip_count, opened_at
) VALUES (
    :pair_key_a, :pair_key_b, :bounce_count, :trip_count, :opened_at
)""",
                    record.model_dump(mode="json"),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(
                    PERSISTENCE_CIRCUIT_BREAKER_SAVE_FAILED,
                )
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
            cursor = await self._db.execute(
                "SELECT pair_key_a, pair_key_b, bounce_count, "
                "trip_count, opened_at FROM circuit_breaker_state "
                "WHERE pair_key_a = ? AND pair_key_b = ?",
                (pair_key_a, pair_key_b),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            return CircuitBreakerStateRecord.model_validate(dict(row))
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
            cursor = await self._db.execute(
                "SELECT pair_key_a, pair_key_b, bounce_count, "
                "trip_count, opened_at FROM circuit_breaker_state "
                "ORDER BY pair_key_a, pair_key_b LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
                results.append(
                    CircuitBreakerStateRecord.model_validate(dict(row)),
                )
            except ValidationError as exc:
                msg = (
                    f"Failed to deserialize circuit breaker state row "
                    f"({row['pair_key_a'] if row else 'unknown'})"
                )
                logger.warning(
                    PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED,
                    pair_key_a=row["pair_key_a"] if row else "unknown",
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
            Tuple of matching rows; empty when no rows match.
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM circuit_breaker_state "
                    "WHERE pair_key_a = ? AND pair_key_b = ?",
                    (pair_key_a, pair_key_b),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(
                    PERSISTENCE_CIRCUIT_BREAKER_DELETE_FAILED,
                )
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
