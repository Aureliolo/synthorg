"""SQLite repository implementation for checkpoint persistence."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.persistence.checkpoint_protocol import CheckpointFilterSpec

logger = get_logger(__name__)


class SQLiteCheckpointRepository:
    """SQLite implementation of the CheckpointRepository protocol.

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

    async def append(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint row (append-only per AppendOnlyRepository).

        A duplicate ``id`` is a contract violation, not an update: a
        plain ``INSERT`` surfaces it as ``DuplicateRecordError`` rather
        than silently overwriting the immutable record.

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
        """
        async with self._write_context():
            try:
                data = checkpoint.model_dump(mode="json")
                # Store created_at in the same canonical ISO-UTC TEXT
                # form purge_before compares against, regardless of the
                # caller's original offset.
                data["created_at"] = format_iso_utc(
                    normalize_utc(checkpoint.created_at),
                )
                await self._db.execute(
                    """\
INSERT INTO checkpoints (
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
                # Roll back so a failed write does not leave the
                # shared connection inside an open transaction for
                # the next caller.
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                if is_unique_constraint_error(exc):
                    msg = f"Checkpoint {checkpoint.id!r} already exists"
                    logger.warning(
                        PERSISTENCE_CHECKPOINT_SAVE_FAILED,
                        checkpoint_id=checkpoint.id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise DuplicateRecordError(msg) from exc
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
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
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
        # use parameterized placeholders -- no injection risk.
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

    async def query(
        self,
        filter_spec: CheckpointFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Checkpoint, ...]:
        """Return checkpoints matching the filter, newest first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CHECKPOINT_QUERY_FAILED
        )
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = (
            "SELECT id, execution_id, agent_id, task_id, "
            "turn_number, context_json, created_at "
            f"FROM checkpoints WHERE {where} "
            "ORDER BY turn_number DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query checkpoints"
            logger.warning(
                PERSISTENCE_CHECKPOINT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete checkpoints with ``created_at < threshold``.

        ``threshold`` must be timezone-aware; a naive value would make
        the cut-off ambiguous against UTC-formatted stored timestamps.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_CHECKPOINT_DELETE_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
            raise QueryError(msg)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM checkpoints WHERE created_at < ?",
                    (format_iso_utc(threshold),),
                )
                count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to purge checkpoints by threshold"
                logger.warning(
                    PERSISTENCE_CHECKPOINT_DELETE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return count

    async def delete_by_execution(self, execution_id: NotBlankStr) -> int:
        """Delete all checkpoints for an execution.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM checkpoints WHERE execution_id = ?",
                    (execution_id,),
                )
                count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``Checkpoint``.
        """
        try:
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
