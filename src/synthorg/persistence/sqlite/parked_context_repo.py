"""SQLite repository implementation for parked agent execution contexts."""

import contextlib
import json
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PARKED_CONTEXT_DELETE_FAILED,
    PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
    PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
    PERSISTENCE_PARKED_CONTEXT_QUERIED,
    PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
    PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001
from synthorg.security.timeout.parked_context import ParkedContext

logger = get_logger(__name__)


class SQLiteParkedContextRepository:
    """SQLite implementation of the ParkedContextRepository protocol.

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

    async def save(self, context: ParkedContext) -> None:
        """Persist a parked context.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
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
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save parked context {context.id!r}"
                logger.warning(
                    PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
                    parked_id=context.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, parked_id: str) -> ParkedContext | None:
        """Retrieve a parked context by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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

        return self._row_to_model(dict(row))

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        """List parked contexts in id order.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED
        )
        try:
            cursor = await self._db.execute(
                "SELECT id, execution_id, agent_id, task_id, approval_id, "
                "parked_at, context_json, metadata "
                "FROM parked_contexts ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list parked contexts"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(dict(row)) for row in rows)
        logger.debug(PERSISTENCE_PARKED_CONTEXT_QUERIED, count=len(results))
        return results

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        """Retrieve a parked context by approval ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None

        return self._row_to_model(dict(row))

    async def get_by_agent(
        self,
        agent_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        """Bounded page of parked contexts for an agent, newest first.

        ``id`` is the stable secondary sort so rows sharing a
        ``parked_at`` page deterministically.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
            agent_id=agent_id,
        )
        try:
            cursor = await self._db.execute(
                "SELECT id, execution_id, agent_id, task_id, approval_id, "
                "parked_at, context_json, metadata "
                "FROM parked_contexts WHERE agent_id = ? "
                "ORDER BY parked_at DESC, id "
                "LIMIT ? OFFSET ?",
                (agent_id, limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query parked contexts for agent {agent_id!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        """Delete a parked context by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM parked_contexts WHERE id = ?",
                    (parked_id,),
                )
                await self._db.commit()
                deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete parked context {parked_id!r}"
                # Use the delete-specific event so audit dashboards
                # can distinguish read-path failures (QUERY_FAILED)
                # from write-path failures.
                logger.warning(
                    PERSISTENCE_PARKED_CONTEXT_DELETE_FAILED,
                    parked_id=parked_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

        return deleted

    def _row_to_model(self, row: dict[str, object]) -> ParkedContext:
        """Convert a database row to a ``ParkedContext`` model.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``ParkedContext``.
        """
        try:
            raw_meta = row.get("metadata")
            if isinstance(raw_meta, str):
                row = {**row, "metadata": json.loads(raw_meta)}
            return ParkedContext.model_validate(row)
        except (ValidationError, json.JSONDecodeError) as exc:
            msg = f"Failed to deserialize parked context {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
                parked_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
