"""Postgres implementation of the CheckpointRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/checkpoint_repo.py.
Postgres stores context_json as native JSONB and timestamps as TIMESTAMPTZ.
"""

import json
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.models import Checkpoint
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CHECKPOINT_DELETE_FAILED,
    PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED,
    PERSISTENCE_CHECKPOINT_NOT_FOUND,
    PERSISTENCE_CHECKPOINT_QUERIED,
    PERSISTENCE_CHECKPOINT_QUERY_FAILED,
    PERSISTENCE_CHECKPOINT_SAVE_FAILED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresCheckpointRepository:
    """Postgres implementation of the CheckpointRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint (upsert).

        ``Checkpoint.context_json`` is a pre-serialized JSON **string**
        at the Python level but the Postgres column is native ``JSONB``.
        psycopg's default string adapter sends ``TEXT`` on the wire and
        Postgres does not implicitly cast ``text`` to ``jsonb``, so we
        parse the string to a structured Python value and let psycopg
        route it through its native JSONB adapter.
        """
        try:
            data = checkpoint.model_dump(mode="json")
            data["context_json"] = Jsonb(json.loads(data["context_json"]))
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO checkpoints (
    id, execution_id, agent_id, task_id, turn_number,
    context_json, created_at
) VALUES (
    %(id)s, %(execution_id)s, %(agent_id)s, %(task_id)s, %(turn_number)s,
    %(context_json)s, %(created_at)s
)
ON CONFLICT(id) DO UPDATE SET
    execution_id=EXCLUDED.execution_id,
    agent_id=EXCLUDED.agent_id,
    task_id=EXCLUDED.task_id,
    turn_number=EXCLUDED.turn_number,
    context_json=EXCLUDED.context_json,
    created_at=EXCLUDED.created_at""",
                    data,
                )
                await conn.commit()
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON in context_json for checkpoint {checkpoint.id!r}"
            logger.warning(
                PERSISTENCE_CHECKPOINT_SAVE_FAILED,
                checkpoint_id=checkpoint.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save checkpoint {checkpoint.id!r}"
            logger.warning(
                PERSISTENCE_CHECKPOINT_SAVE_FAILED,
                checkpoint_id=checkpoint.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
            logger.warning(
                PERSISTENCE_CHECKPOINT_QUERY_FAILED,
                execution_id=execution_id,
                task_id=task_id,
                error=msg,
            )
            raise ValueError(msg)

        conditions: list[str] = []
        params: list[str] = []

        if execution_id is not None:
            conditions.append("execution_id = %s")
            params.append(execution_id)
        if task_id is not None:
            conditions.append("task_id = %s")
            params.append(task_id)

        where = " AND ".join(conditions)
        # where is built from hardcoded column names; only values use parameterized
        # placeholders -- no injection risk.
        # ruff: noqa: S608
        query = (
            "SELECT id, execution_id, agent_id, task_id, "
            "turn_number, context_json, created_at "
            f"FROM checkpoints WHERE {where} "
            "ORDER BY turn_number DESC LIMIT 1"
        )

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to query latest checkpoint"
            logger.warning(
                PERSISTENCE_CHECKPOINT_QUERY_FAILED,
                execution_id=execution_id,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM checkpoints WHERE execution_id = %s",
                    (execution_id,),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete checkpoints for execution {execution_id!r}"
            logger.warning(
                PERSISTENCE_CHECKPOINT_DELETE_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return count

    def _row_to_model(self, row: dict[str, object]) -> Checkpoint:
        """Convert a database row to a ``Checkpoint`` model.

        ``context_json`` comes back from Postgres JSONB as a Python
        dict/list, but the ``Checkpoint`` model defines the field as
        ``str`` (pre-serialized JSON). Re-serialize before validation
        so the round-trip is lossless.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw = row.get("context_json")
            if raw is not None and not isinstance(raw, str):
                row["context_json"] = json.dumps(raw)
            return Checkpoint.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize checkpoint {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED,
                checkpoint_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
