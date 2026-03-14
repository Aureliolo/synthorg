"""SQLite repository implementation for heartbeat persistence."""

import sqlite3
from datetime import UTC

import aiosqlite
from pydantic import AwareDatetime, ValidationError

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.models import Heartbeat
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_HEARTBEAT_DELETE_FAILED,
    PERSISTENCE_HEARTBEAT_DELETED,
    PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED,
    PERSISTENCE_HEARTBEAT_NOT_FOUND,
    PERSISTENCE_HEARTBEAT_QUERIED,
    PERSISTENCE_HEARTBEAT_QUERY_FAILED,
    PERSISTENCE_HEARTBEAT_SAVE_FAILED,
    PERSISTENCE_HEARTBEAT_SAVED,
)
from synthorg.persistence.errors import QueryError

logger = get_logger(__name__)


class SQLiteHeartbeatRepository:
    """SQLite implementation of the HeartbeatRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, heartbeat: Heartbeat) -> None:
        """Persist a heartbeat (upsert by execution_id)."""
        try:
            data = heartbeat.model_dump(mode="json")
            # Normalize to UTC so lexicographic comparisons in
            # get_stale() work correctly regardless of input timezone.
            data["last_heartbeat_at"] = heartbeat.last_heartbeat_at.astimezone(
                UTC
            ).isoformat()
            await self._db.execute(
                """\
INSERT OR REPLACE INTO heartbeats (
    execution_id, agent_id, task_id, last_heartbeat_at
) VALUES (
    :execution_id, :agent_id, :task_id, :last_heartbeat_at
)""",
                data,
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save heartbeat for execution {heartbeat.execution_id!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_SAVE_FAILED,
                execution_id=heartbeat.execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_HEARTBEAT_SAVED,
            execution_id=heartbeat.execution_id,
        )

    async def get(self, execution_id: NotBlankStr) -> Heartbeat | None:
        """Retrieve a heartbeat by execution ID."""
        try:
            cursor = await self._db.execute(
                "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                "FROM heartbeats WHERE execution_id = ?",
                (execution_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        threshold_iso = threshold.astimezone(UTC).isoformat()
        try:
            cursor = await self._db.execute(
                "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                "FROM heartbeats WHERE last_heartbeat_at < ? "
                "ORDER BY last_heartbeat_at",
                (threshold_iso,),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            cursor = await self._db.execute(
                "DELETE FROM heartbeats WHERE execution_id = ?",
                (execution_id,),
            )
            deleted = cursor.rowcount > 0
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete heartbeat {execution_id!r}"
            logger.exception(
                PERSISTENCE_HEARTBEAT_DELETE_FAILED,
                execution_id=execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if deleted:
            logger.debug(
                PERSISTENCE_HEARTBEAT_DELETED,
                execution_id=execution_id,
            )
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
