"""SQLite repository implementation for heartbeat persistence."""

import contextlib
import sqlite3
from datetime import UTC, datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.checkpoint.models import Heartbeat
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.heartbeat import (
    PERSISTENCE_HEARTBEAT_DELETE_FAILED,
    PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED,
    PERSISTENCE_HEARTBEAT_NOT_FOUND,
    PERSISTENCE_HEARTBEAT_QUERIED,
    PERSISTENCE_HEARTBEAT_QUERY_FAILED,
    PERSISTENCE_HEARTBEAT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class SQLiteHeartbeatRepository:
    """SQLite implementation of the HeartbeatRepository protocol.

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

    async def save(self, heartbeat: Heartbeat) -> None:
        """Persist a heartbeat (upsert by execution_id).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
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
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = (
                    f"Failed to save heartbeat for execution {heartbeat.execution_id!r}"
                )
                logger.warning(
                    PERSISTENCE_HEARTBEAT_SAVE_FAILED,
                    execution_id=heartbeat.execution_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, execution_id: NotBlankStr) -> Heartbeat | None:
        """Retrieve a heartbeat by execution ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                "FROM heartbeats WHERE execution_id = ?",
                (execution_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query heartbeat {execution_id!r}"
            logger.warning(
                PERSISTENCE_HEARTBEAT_QUERY_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_HEARTBEAT_NOT_FOUND,
                execution_id=execution_id,
            )
            return None

        return self._row_to_model(dict(row))

    async def get_stale(
        self,
        threshold: datetime,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Heartbeat, ...]:
        """Bounded page of heartbeats older than the threshold.

        ``execution_id`` is the stable secondary sort so rows sharing
        a ``last_heartbeat_at`` page deterministically.

        Args:
            threshold: Heartbeats with ``last_heartbeat_at`` before
                this timestamp are considered stale. Must be
                timezone-aware; a naive value is rejected.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_HEARTBEAT_QUERY_FAILED
        )
        threshold_iso = threshold.astimezone(UTC).isoformat()
        try:
            async with self._db.execute(
                "SELECT execution_id, agent_id, task_id, last_heartbeat_at "
                "FROM heartbeats WHERE last_heartbeat_at < ? "
                "ORDER BY last_heartbeat_at, execution_id "
                "LIMIT ? OFFSET ?",
                (threshold_iso, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query stale heartbeats"
            logger.warning(
                PERSISTENCE_HEARTBEAT_QUERY_FAILED,
                threshold=threshold,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        """Delete a heartbeat by execution ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM heartbeats WHERE execution_id = ?",
                    (execution_id,),
                ) as cursor:
                    deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete heartbeat {execution_id!r}"
                logger.warning(
                    PERSISTENCE_HEARTBEAT_DELETE_FAILED,
                    execution_id=execution_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    def _row_to_model(self, row: dict[str, object]) -> Heartbeat:
        """Convert a database row to a ``Heartbeat`` model.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``Heartbeat``.
        """
        try:
            return Heartbeat.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize heartbeat {row.get('execution_id')!r}"
            logger.warning(
                PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED,
                execution_id=row.get("execution_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
