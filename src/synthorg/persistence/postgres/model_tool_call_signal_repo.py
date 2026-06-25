"""Postgres implementation of the ModelToolCallSignalRepository protocol.

The Postgres sibling of
src/synthorg/persistence/sqlite/model_tool_call_signal_repo.py. Postgres
stores ``failure_score`` and ``decayed_at`` as DOUBLE PRECISION.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.model_tool_call_signal import (
    PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_DELETE_FAILED,
    PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED,
    PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignal,
    ModelToolCallSignalKey,
)

logger = get_logger(__name__)


class PostgresModelToolCallSignalRepository:
    """Postgres implementation of the ModelToolCallSignalRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ModelToolCallSignal) -> None:
        """Persist a signal record (upsert by composite key).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO model_tool_call_signals (
    provider_name, model_id, failure_score, decayed_at
) VALUES (
    %(provider_name)s, %(model_id)s, %(failure_score)s, %(decayed_at)s
)
ON CONFLICT(provider_name, model_id) DO UPDATE SET
    failure_score=EXCLUDED.failure_score,
    decayed_at=EXCLUDED.decayed_at""",
                    entity.model_dump(mode="json"),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to save tool-call signal for "
                f"{entity.provider_name}/{entity.model_id}"
            )
            logger.warning(
                PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_SAVE_FAILED,
                provider_name=entity.provider_name,
                model_id=entity.model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        entity_id: ModelToolCallSignalKey,
    ) -> ModelToolCallSignal | None:
        """Retrieve one signal record by composite key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        provider_name, model_id = entity_id
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT provider_name, model_id, failure_score, decayed_at "
                    "FROM model_tool_call_signals "
                    "WHERE provider_name = %s AND model_id = %s",
                    (provider_name, model_id),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch tool-call signal for {provider_name}/{model_id}"
            logger.warning(
                PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED,
                provider_name=provider_name,
                model_id=model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None
        try:
            return ModelToolCallSignal.model_validate(row)
        except ValidationError as exc:
            msg = (
                f"Failed to deserialize tool-call signal row {provider_name}/{model_id}"
            )
            logger.warning(
                PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED,
                provider_name=provider_name,
                model_id=model_id,
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
    ) -> tuple[ModelToolCallSignal, ...]:
        """List records ordered by ``(provider_name, model_id)`` ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT provider_name, model_id, failure_score, decayed_at "
                    "FROM model_tool_call_signals "
                    "ORDER BY provider_name, model_id LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list tool-call signals"
            logger.warning(
                PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[ModelToolCallSignal] = []
        for row in rows:
            try:
                results.append(ModelToolCallSignal.model_validate(row))
            except ValidationError as exc:
                msg = (
                    f"Failed to deserialize tool-call signal row "
                    f"({row.get('provider_name') if row else 'unknown'})"
                )
                logger.warning(
                    PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED,
                    provider_name=row.get("provider_name") if row else "unknown",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="deserialization failed",
                )
                raise QueryError(msg) from exc

        return tuple(results)

    async def delete(self, entity_id: ModelToolCallSignalKey) -> bool:
        """Delete a signal record by composite key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        provider_name, model_id = entity_id
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM model_tool_call_signals "
                    "WHERE provider_name = %s AND model_id = %s",
                    (provider_name, model_id),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete tool-call signal for {provider_name}/{model_id}"
            logger.warning(
                PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_DELETE_FAILED,
                provider_name=provider_name,
                model_id=model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted
