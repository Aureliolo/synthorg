"""SQLite repository implementation for agent runtime state persistence."""

import asyncio
import contextlib
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.enums import ExecutionStatus
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AGENT_STATE_ACTIVE_QUERIED,
    PERSISTENCE_AGENT_STATE_ACTIVE_QUERY_FAILED,
    PERSISTENCE_AGENT_STATE_DELETE_FAILED,
    PERSISTENCE_AGENT_STATE_DESERIALIZE_FAILED,
    PERSISTENCE_AGENT_STATE_FETCH_FAILED,
    PERSISTENCE_AGENT_STATE_FETCHED,
    PERSISTENCE_AGENT_STATE_NOT_FOUND,
    PERSISTENCE_AGENT_STATE_SAVE_FAILED,
)

logger = get_logger(__name__)


class SQLiteAgentStateRepository:
    """SQLite implementation of the AgentStateRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, state: AgentRuntimeState) -> None:
        """Persist an agent runtime state (upsert by agent_id)."""
        async with self._write_lock:
            try:
                data = state.model_dump(mode="json")
                await self._db.execute(
                    """\
INSERT OR REPLACE INTO agent_states (
    agent_id, execution_id, task_id, status, turn_count,
    accumulated_cost, currency, last_activity_at, started_at
) VALUES (
    :agent_id, :execution_id, :task_id, :status, :turn_count,
    :accumulated_cost, :currency, :last_activity_at, :started_at
)""",
                    data,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save agent state for {state.agent_id!r}"
                logger.warning(
                    PERSISTENCE_AGENT_STATE_SAVE_FAILED,
                    agent_id=state.agent_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, agent_id: NotBlankStr) -> AgentRuntimeState | None:
        """Retrieve an agent runtime state by agent ID."""
        try:
            cursor = await self._db.execute(
                "SELECT agent_id, execution_id, task_id, status, "
                "turn_count, accumulated_cost, currency, "
                "last_activity_at, started_at "
                "FROM agent_states WHERE agent_id = ?",
                (agent_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch agent state for {agent_id!r}"
            logger.warning(
                PERSISTENCE_AGENT_STATE_FETCH_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_AGENT_STATE_NOT_FOUND,
                agent_id=agent_id,
            )
            return None

        state = self._row_to_model(dict(row))
        logger.debug(
            PERSISTENCE_AGENT_STATE_FETCHED,
            agent_id=state.agent_id,
            status=state.status.value,
        )
        return state

    async def get_active(self) -> tuple[AgentRuntimeState, ...]:
        """Retrieve all non-idle agent states, ordered by last_activity_at DESC."""
        try:
            cursor = await self._db.execute(
                "SELECT agent_id, execution_id, task_id, status, "
                "turn_count, accumulated_cost, currency, "
                "last_activity_at, started_at "
                "FROM agent_states WHERE status != ? "
                "ORDER BY last_activity_at DESC",
                (ExecutionStatus.IDLE.value,),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query active agent states"
            logger.warning(
                PERSISTENCE_AGENT_STATE_ACTIVE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        states = tuple(self._row_to_model(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_AGENT_STATE_ACTIVE_QUERIED,
            count=len(states),
        )
        return states

    async def delete(self, agent_id: NotBlankStr) -> bool:
        """Delete an agent runtime state by agent ID."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM agent_states WHERE agent_id = ?",
                    (agent_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete agent state for {agent_id!r}"
                logger.warning(
                    PERSISTENCE_AGENT_STATE_DELETE_FAILED,
                    agent_id=agent_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if not deleted:
            logger.debug(
                PERSISTENCE_AGENT_STATE_NOT_FOUND,
                agent_id=agent_id,
            )
        return deleted

    def _row_to_model(self, row: dict[str, object]) -> AgentRuntimeState:
        """Convert a database row to an ``AgentRuntimeState`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            return AgentRuntimeState.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize agent state {row.get('agent_id')!r}"
            logger.warning(
                PERSISTENCE_AGENT_STATE_DESERIALIZE_FAILED,
                agent_id=row.get("agent_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
