"""Postgres repository implementation for agent runtime state persistence."""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
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
    PERSISTENCE_AGENT_STATE_LIST_FAILED,
    PERSISTENCE_AGENT_STATE_LISTED,
    PERSISTENCE_AGENT_STATE_NOT_FOUND,
    PERSISTENCE_AGENT_STATE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresAgentStateRepository:
    """Postgres implementation of the AgentStateRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, state: AgentRuntimeState) -> None:
        """Persist an agent runtime state (upsert by agent_id)."""
        try:
            data = state.model_dump(mode="json")
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO agent_states (
    agent_id, execution_id, task_id, status, turn_count,
    accumulated_cost, currency, last_activity_at, started_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (agent_id) DO UPDATE SET
    execution_id = EXCLUDED.execution_id,
    task_id = EXCLUDED.task_id,
    status = EXCLUDED.status,
    turn_count = EXCLUDED.turn_count,
    accumulated_cost = EXCLUDED.accumulated_cost,
    currency = EXCLUDED.currency,
    last_activity_at = EXCLUDED.last_activity_at,
    started_at = EXCLUDED.started_at
""",
                    (
                        data["agent_id"],
                        data["execution_id"],
                        data["task_id"],
                        data["status"],
                        data["turn_count"],
                        data["accumulated_cost"],
                        data["currency"],
                        data["last_activity_at"],
                        data["started_at"],
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT agent_id, execution_id, task_id, status, "
                    "turn_count, accumulated_cost, currency, "
                    "last_activity_at, started_at "
                    "FROM agent_states WHERE agent_id = %s",
                    (agent_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

        state = self._row_to_model(row)
        logger.debug(
            PERSISTENCE_AGENT_STATE_FETCHED,
            agent_id=state.agent_id,
            status=state.status.value,
        )
        return state

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        """List agent runtime states in agent_id order."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT agent_id, execution_id, task_id, status, "
                    "turn_count, accumulated_cost, currency, "
                    "last_activity_at, started_at "
                    "FROM agent_states ORDER BY agent_id LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list agent states"
            logger.warning(
                PERSISTENCE_AGENT_STATE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        states = tuple(self._row_to_model(row) for row in rows)
        logger.debug(PERSISTENCE_AGENT_STATE_LISTED, count=len(states))
        return states

    async def get_active(self) -> tuple[AgentRuntimeState, ...]:
        """Retrieve all non-idle agent states, ordered by last_activity_at DESC."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT agent_id, execution_id, task_id, status, "
                    "turn_count, accumulated_cost, currency, "
                    "last_activity_at, started_at "
                    "FROM agent_states WHERE status != %s "
                    "ORDER BY last_activity_at DESC",
                    (ExecutionStatus.IDLE.value,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query active agent states"
            logger.warning(
                PERSISTENCE_AGENT_STATE_ACTIVE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        states = tuple(self._row_to_model(row) for row in rows)
        logger.debug(
            PERSISTENCE_AGENT_STATE_ACTIVE_QUERIED,
            count=len(states),
        )
        return states

    async def delete(self, agent_id: NotBlankStr) -> bool:
        """Delete an agent runtime state by agent ID."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM agent_states WHERE agent_id = %s",
                    (agent_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
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
