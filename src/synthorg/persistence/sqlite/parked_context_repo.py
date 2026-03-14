"""SQLite repository implementation for parked agent execution contexts."""

import json
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_PARKED_CONTEXT_DELETED,
    PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
    PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
    PERSISTENCE_PARKED_CONTEXT_QUERIED,
    PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
    PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
    PERSISTENCE_PARKED_CONTEXT_SAVED,
)
from synthorg.persistence.errors import QueryError
from synthorg.security.timeout.parked_context import ParkedContext

logger = get_logger(__name__)


class SQLiteParkedContextRepository:
    """SQLite implementation of the ParkedContextRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, context: ParkedContext) -> None:
        """Persist a parked context."""
        try:
            data = context.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT OR REPLACE INTO parked_contexts (
    id, execution_id, agent_id, task_id, approval_id,
    parked_at, context_json, metadata
) VALUES (
    :id, :execution_id, :agent_id, :task_id, :approval_id,
    :parked_at, :context_json, :metadata
)""",
                {**data, "metadata": json.dumps(data["metadata"])},
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save parked context {context.id!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
                parked_id=context.id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PARKED_CONTEXT_SAVED,
            parked_id=context.id,
            agent_id=context.agent_id,
        )

    async def get(self, parked_id: str) -> ParkedContext | None:
        """Retrieve a parked context by ID."""
        try:
            cursor = await self._db.execute(
                "SELECT id, execution_id, agent_id, task_id, approval_id, "
                "parked_at, context_json, metadata "
                "FROM parked_contexts WHERE id = ?",
                (parked_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query parked context {parked_id!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                parked_id=parked_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
                parked_id=parked_id,
            )
            return None

        return self._row_to_model(dict(row))

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        """Retrieve a parked context by approval ID."""
        try:
            cursor = await self._db.execute(
                "SELECT id, execution_id, agent_id, task_id, approval_id, "
                "parked_at, context_json, metadata "
                "FROM parked_contexts WHERE approval_id = ?",
                (approval_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query parked context by approval {approval_id!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                approval_id=approval_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None

        return self._row_to_model(dict(row))

    async def get_by_agent(self, agent_id: str) -> tuple[ParkedContext, ...]:
        """Retrieve all parked contexts for an agent."""
        try:
            cursor = await self._db.execute(
                "SELECT id, execution_id, agent_id, task_id, approval_id, "
                "parked_at, context_json, metadata "
                "FROM parked_contexts WHERE agent_id = ? "
                "ORDER BY parked_at DESC",
                (agent_id,),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query parked contexts for agent {agent_id!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                agent_id=agent_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(dict(row)) for row in rows)

        logger.debug(
            PERSISTENCE_PARKED_CONTEXT_QUERIED,
            agent_id=agent_id,
            count=len(results),
        )
        return results

    async def delete(self, parked_id: str) -> bool:
        """Delete a parked context by ID."""
        try:
            cursor = await self._db.execute(
                "DELETE FROM parked_contexts WHERE id = ?",
                (parked_id,),
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete parked context {parked_id!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                parked_id=parked_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug(
                PERSISTENCE_PARKED_CONTEXT_DELETED,
                parked_id=parked_id,
            )
        return deleted

    def _row_to_model(self, row: dict[str, object]) -> ParkedContext:
        """Convert a database row to a ``ParkedContext`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw_meta = row.get("metadata")
            if isinstance(raw_meta, str):
                row = {**row, "metadata": json.loads(raw_meta)}
            return ParkedContext.model_validate(row)
        except (ValidationError, json.JSONDecodeError) as exc:
            msg = f"Failed to deserialize parked context {row.get('id')!r}"
            logger.exception(
                PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
                parked_id=row.get("id"),
                error=str(exc),
            )
            raise QueryError(msg) from exc
