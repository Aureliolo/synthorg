"""Postgres implementation of the HeartbeatRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/heartbeat_repo.py.
Postgres stores timestamps as native TIMESTAMPTZ columns (SQLite uses ISO strings).
"""

from datetime import UTC
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from pydantic import AwareDatetime, ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.models import Heartbeat
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_HEARTBEAT_DELETE_FAILED,
    PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED,
    PERSISTENCE_HEARTBEAT_NOT_FOUND,
    PERSISTENCE_HEARTBEAT_QUERIED,
    PERSISTENCE_HEARTBEAT_QUERY_FAILED,
    PERSISTENCE_HEARTBEAT_SAVE_FAILED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresHeartbeatRepository:
    """Postgres implementation of the HeartbeatRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, heartbeat: Heartbeat) -> None:
        """Persist a heartbeat (upsert by execution_id)."""
        # Normalize to UTC for consistent lexicographic comparisons
        last_heartbeat_at_utc = heartbeat.last_heartbeat_at.astimezone(UTC)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO heartbeats (
    execution_id, agent_id, task_id, last_heartbeat_at
) VALUES (
    %s, %s, %s, %s
)
ON CONFLICT(execution_id) DO UPDATE SET
    agent_id=EXCLUDED.agent_id,
    task_id=EXCLUDED.task_id,
    last_heartbeat_at=EXCLUDED.last_heartbeat_at""",
                    (
                        heartbeat.execution_id,
                        heartbeat.agent_id,
                        heartbeat.task_id,
                        last_heartbeat_at_utc,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save heartbeat for execution {heartbeat.execution_id!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_SAVE_FAILED,
                execution_id=heartbeat.execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, execution_id: NotBlankStr) -> Heartbeat | None:
        """Retrieve a heartbeat by execution ID."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                    "FROM heartbeats WHERE execution_id = %s",
                    (execution_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to query heartbeat {execution_id!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_QUERY_FAILED,
                execution_id=execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_HEARTBEAT_NOT_FOUND,
                execution_id=execution_id,
            )
            return None

        return self._row_to_model(dict(row))

    async def get_stale(self, threshold: AwareDatetime) -> tuple[Heartbeat, ...]:
        """Retrieve heartbeats older than the threshold.

        Args:
            threshold: Heartbeats with ``last_heartbeat_at`` before
                this timestamp are considered stale.
        """
        threshold_utc = threshold.astimezone(UTC)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                    "FROM heartbeats WHERE last_heartbeat_at < %s "
                    "ORDER BY last_heartbeat_at",
                    (threshold_utc,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query stale heartbeats"
            logger.exception(
                PERSISTENCE_HEARTBEAT_QUERY_FAILED,
                threshold=threshold,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_HEARTBEAT_QUERIED,
            threshold=threshold,
            count=len(results),
        )
        return results

    async def delete(self, execution_id: NotBlankStr) -> bool:
        """Delete a heartbeat by execution ID."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM heartbeats WHERE execution_id = %s",
                    (execution_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete heartbeat {execution_id!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_DELETE_FAILED,
                execution_id=execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        return deleted

    def _row_to_model(self, row: dict[str, object]) -> Heartbeat:
        """Convert a database row to a ``Heartbeat`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            return Heartbeat.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize heartbeat {row.get('execution_id')!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED,
                execution_id=row.get("execution_id"),
                error=str(exc),
            )
            raise QueryError(msg) from exc
