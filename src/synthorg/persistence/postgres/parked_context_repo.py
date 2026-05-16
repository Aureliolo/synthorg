"""Postgres implementation of the ParkedContextRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/parked_context_repo.py.
Postgres stores context_json and metadata as native JSONB columns. The repository
handles direct JSONB usage without manual JSON serialization.
"""

import json
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
    PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
    PERSISTENCE_PARKED_CONTEXT_QUERIED,
    PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
    PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.security.timeout.parked_context import ParkedContext

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresParkedContextRepository:
    """Postgres implementation of the ParkedContextRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, context: ParkedContext) -> None:
        """Persist a parked context.

        ``ParkedContext.context_json`` is a pre-serialized JSON
        **string** at the Python level but the Postgres column is
        native ``JSONB``.  psycopg sends bare strings as ``TEXT`` and
        Postgres does not implicitly cast ``text`` to ``jsonb``, so we
        parse the string into a Python object and wrap it in
        ``Jsonb(...)`` for the native wire format.
        """
        try:
            data = context.model_dump(mode="json")
            data["context_json"] = Jsonb(json.loads(data["context_json"]))
            # ``metadata`` is a plain ``dict`` on the Pydantic model but the
            # Postgres column is ``JSONB``; psycopg cannot adapt raw dicts,
            # so wrap explicitly.
            data["metadata"] = Jsonb(data.get("metadata") or {})
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO parked_contexts (
    id, execution_id, agent_id, task_id, approval_id,
    parked_at, context_json, metadata
) VALUES (
    %(id)s, %(execution_id)s, %(agent_id)s, %(task_id)s, %(approval_id)s,
    %(parked_at)s, %(context_json)s, %(metadata)s
)
ON CONFLICT(id) DO UPDATE SET
    execution_id=EXCLUDED.execution_id,
    agent_id=EXCLUDED.agent_id,
    task_id=EXCLUDED.task_id,
    approval_id=EXCLUDED.approval_id,
    parked_at=EXCLUDED.parked_at,
    context_json=EXCLUDED.context_json,
    metadata=EXCLUDED.metadata""",
                    data,
                )
                await conn.commit()
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON in context_json for parked context {context.id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
                parked_id=context.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save parked context {context.id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
                parked_id=context.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, parked_id: NotBlankStr) -> ParkedContext | None:
        """Retrieve a parked context by ID."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, execution_id, agent_id, task_id, approval_id, "
                    "parked_at, context_json, metadata "
                    "FROM parked_contexts WHERE id = %s",
                    (parked_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to query parked context {parked_id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                parked_id=parked_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
                parked_id=parked_id,
            )
            return None

        return self._row_to_model(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        """List parked contexts in id order."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, execution_id, agent_id, task_id, approval_id, "
                    "parked_at, context_json, metadata "
                    "FROM parked_contexts ORDER BY id LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list parked contexts"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(row) for row in rows)
        logger.debug(PERSISTENCE_PARKED_CONTEXT_QUERIED, count=len(results))
        return results

    async def get_by_approval(self, approval_id: NotBlankStr) -> ParkedContext | None:
        """Retrieve a parked context by approval ID."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, execution_id, agent_id, task_id, approval_id, "
                    "parked_at, context_json, metadata "
                    "FROM parked_contexts WHERE approval_id = %s",
                    (approval_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to query parked context by approval {approval_id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None

        return self._row_to_model(row)

    async def get_by_agent(self, agent_id: NotBlankStr) -> tuple[ParkedContext, ...]:
        """Retrieve all parked contexts for an agent."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, execution_id, agent_id, task_id, approval_id, "
                    "parked_at, context_json, metadata "
                    "FROM parked_contexts WHERE agent_id = %s "
                    "ORDER BY parked_at DESC",
                    (agent_id,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to query parked contexts for agent {agent_id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(row) for row in rows)

        logger.debug(
            PERSISTENCE_PARKED_CONTEXT_QUERIED,
            agent_id=agent_id,
            count=len(results),
        )
        return results

    async def delete(self, parked_id: NotBlankStr) -> bool:
        """Delete a parked context by ID."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM parked_contexts WHERE id = %s",
                    (parked_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete parked context {parked_id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                parked_id=parked_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted

    def _row_to_model(self, row: dict[str, object]) -> ParkedContext:
        """Convert a database row to a ``ParkedContext`` model.

        ``context_json`` comes back from Postgres JSONB as a Python
        dict/list, but the ``ParkedContext`` model defines the field
        as ``str`` (pre-serialized JSON). Re-serialize before
        validation so the round-trip is lossless.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw = row.get("context_json")
            if raw is not None and not isinstance(raw, str):
                row["context_json"] = json.dumps(raw)
            return ParkedContext.model_validate(row)
        except (ValidationError, ValueError) as exc:
            msg = f"Failed to deserialize parked context {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
                parked_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
