"""SQLite repository implementation for checkpoint persistence."""
# ruff: noqa: S608 — dynamic WHERE built from hardcoded column names only

import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.models import Checkpoint
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_CHECKPOINT_DELETE_FAILED,
    PERSISTENCE_CHECKPOINT_DELETED,
    PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED,
    PERSISTENCE_CHECKPOINT_NOT_FOUND,
    PERSISTENCE_CHECKPOINT_QUERIED,
    PERSISTENCE_CHECKPOINT_QUERY_FAILED,
    PERSISTENCE_CHECKPOINT_SAVE_FAILED,
    PERSISTENCE_CHECKPOINT_SAVED,
)
from synthorg.persistence.errors import QueryError

logger = get_logger(__name__)


class SQLiteCheckpointRepository:
    """SQLite implementation of the CheckpointRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint (upsert)."""
        try:
            data = checkpoint.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT OR REPLACE INTO checkpoints (
    id, execution_id, agent_id, task_id, turn_number,
    context_json, created_at
) VALUES (
    :id, :execution_id, :agent_id, :task_id, :turn_number,
    :context_json, :created_at
)""",
                data,
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save checkpoint {checkpoint.id!r}"
            logger.exception(
                PERSISTENCE_CHECKPOINT_SAVE_FAILED,
                checkpoint_id=checkpoint.id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_CHECKPOINT_SAVED,
            checkpoint_id=checkpoint.id,
            execution_id=checkpoint.execution_id,
            turn_number=checkpoint.turn_number,
        )

    async def get_latest(
        self,
        *,
        execution_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> Checkpoint | None:
        """Retrieve the latest checkpoint by turn_number.

        At least one filter is required.

        Raises:
            ValueError: If neither filter is provided.
        """
        if execution_id is None and task_id is None:
            msg = "At least one of execution_id or task_id is required"
            raise ValueError(msg)

        conditions: list[str] = []
        params: list[str] = []

        if execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(execution_id)
        if task_id is not None:
            conditions.append("task_id = ?")
            params.append(task_id)

        where = " AND ".join(conditions)
        # where is built from hardcoded column names; only values
        # use parameterized placeholders — no injection risk.
        query = (
            "SELECT id, execution_id, agent_id, task_id, "
            "turn_number, context_json, created_at "
            f"FROM checkpoints WHERE {where} "
            "ORDER BY turn_number DESC LIMIT 1"
        )

        try:
            cursor = await self._db.execute(query, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query latest checkpoint"
            logger.exception(
                PERSISTENCE_CHECKPOINT_QUERY_FAILED,
                execution_id=execution_id,
                task_id=task_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_CHECKPOINT_NOT_FOUND,
                execution_id=execution_id,
                task_id=task_id,
            )
            return None

        checkpoint = self._row_to_model(dict(row))
        logger.debug(
            PERSISTENCE_CHECKPOINT_QUERIED,
            checkpoint_id=checkpoint.id,
            turn_number=checkpoint.turn_number,
        )
        return checkpoint

    async def delete_by_execution(self, execution_id: NotBlankStr) -> int:
        """Delete all checkpoints for an execution."""
        try:
            cursor = await self._db.execute(
                "DELETE FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            )
            count = cursor.rowcount
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete checkpoints for execution {execution_id!r}"
            logger.exception(
                PERSISTENCE_CHECKPOINT_DELETE_FAILED,
                execution_id=execution_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if count > 0:
            logger.debug(
                PERSISTENCE_CHECKPOINT_DELETED,
                execution_id=execution_id,
                count=count,
            )
        return count

    def _row_to_model(self, row: dict[str, object]) -> Checkpoint:
        """Convert a database row to a ``Checkpoint`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            return Checkpoint.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize checkpoint {row.get('id')!r}"
            logger.exception(
                PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED,
                checkpoint_id=row.get("id"),
                error=str(exc),
            )
            raise QueryError(msg) from exc
