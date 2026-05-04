"""SQLite repository implementation for WorkflowExecution."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    import aiosqlite

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FETCH_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FETCHED,
    PERSISTENCE_WORKFLOW_EXEC_FIND_BY_TASK_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK,
    PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_LISTED,
    PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
)

logger = get_logger(__name__)

_SELECT_COLUMNS = """\
id, definition_id, definition_revision, status, node_executions,
activated_by, project, created_at, updated_at, completed_at,
error, version"""

_MAX_LIST_ROWS: int = 10_000
"""Safety cap on list query results pending pagination support."""


def _parse_row_timestamps(data: dict[str, object]) -> None:
    """Parse ISO timestamps and ensure timezone awareness.

    Mutates ``data`` in-place.
    """
    for field in ("created_at", "updated_at"):
        dt = datetime.fromisoformat(str(data[field]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        data[field] = dt
    if data.get("completed_at") is not None:
        dt = datetime.fromisoformat(str(data["completed_at"]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        data["completed_at"] = dt


def _deserialize_node_executions(
    raw_json: str,
) -> tuple[WorkflowNodeExecution, ...]:
    """Deserialize JSON array into WorkflowNodeExecution tuple."""
    items = json.loads(raw_json)
    return tuple(
        WorkflowNodeExecution(
            node_id=item["node_id"],
            node_type=WorkflowNodeType(item["node_type"]),
            status=WorkflowNodeExecutionStatus(item["status"]),
            task_id=item.get("task_id"),
            skipped_reason=item.get("skipped_reason"),
        )
        for item in items
    )


def _deserialize_row(
    row: aiosqlite.Row,
    context_id: str,
) -> WorkflowExecution:
    """Reconstruct a ``WorkflowExecution`` from a database row.

    Args:
        row: A single database row with execution columns.
        context_id: Identifier for error context logging.

    Returns:
        Validated ``WorkflowExecution`` model instance.

    Raises:
        QueryError: If deserialization fails.
    """
    try:
        data = dict(row)
        data["status"] = WorkflowExecutionStatus(data["status"])
        data["node_executions"] = _deserialize_node_executions(
            data["node_executions"],
        )
        _parse_row_timestamps(data)
        return WorkflowExecution.model_validate(data)
    except (
        TypeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        msg = f"Failed to deserialize workflow execution {context_id!r}"
        logger.warning(
            PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED,
            execution_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class SQLiteWorkflowExecutionRepository:
    """SQLite-backed workflow execution repository.

    Provides CRUD operations for ``WorkflowExecution`` models using
    a shared ``aiosqlite.Connection``.  Node executions are stored
    as a JSON array.  All write operations commit immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
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

    async def save(self, execution: WorkflowExecution) -> None:
        """Persist a workflow execution (insert or update).

        Uses explicit create/update branches rather than upsert
        to avoid version-conflict misclassification.

        Args:
            execution: Workflow execution model to persist.

        Raises:
            DuplicateRecordError: If inserting a duplicate ID.
            PersistenceVersionConflictError: If optimistic concurrency check fails.
            QueryError: If the database operation fails.
        """
        if execution.version == 1:
            await self._insert(execution)
        else:
            await self._update(execution)

    def _serialize_execution(
        self,
        execution: WorkflowExecution,
    ) -> tuple[object, ...]:
        """Build the parameter tuple for insert/update SQL."""
        node_json = json.dumps(
            [ne.model_dump(mode="json") for ne in execution.node_executions],
        )
        completed_iso = (
            execution.completed_at.astimezone(UTC).isoformat()
            if execution.completed_at is not None
            else None
        )
        return (
            execution.id,
            execution.definition_id,
            execution.definition_revision,
            execution.status.value,
            node_json,
            execution.activated_by,
            execution.project,
            execution.created_at.astimezone(UTC).isoformat(),
            execution.updated_at.astimezone(UTC).isoformat(),
            completed_iso,
            execution.error,
            execution.version,
        )

    async def _insert(self, execution: WorkflowExecution) -> None:
        """Insert a new workflow execution row."""
        params = self._serialize_execution(execution)
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    """\
INSERT INTO workflow_executions
    (id, definition_id, definition_revision, status, node_executions,
     activated_by, project, created_at, updated_at, completed_at,
     error, version)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    params,
                )
                if cursor.rowcount == 0:
                    msg = f"Workflow execution {execution.id!r} already exists"
                    logger.warning(
                        PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                        execution_id=execution.id,
                        error=msg,
                    )
                    raise DuplicateRecordError(msg)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._db.rollback()
                err_text = str(exc).lower()
                if "unique" in err_text or "primary key" in err_text:
                    msg = (
                        f"Workflow execution {execution.id!r} already exists "
                        "(duplicate ID)"
                    )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                        execution_id=execution.id,
                        error=msg,
                    )
                    raise DuplicateRecordError(msg) from exc
                msg = f"Integrity error saving workflow execution {execution.id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                    execution_id=execution.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to save workflow execution {execution.id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                    execution_id=execution.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def _update(self, execution: WorkflowExecution) -> None:
        """Update an existing workflow execution with version check."""
        params = self._serialize_execution(execution)
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    """\
UPDATE workflow_executions SET
    definition_id=?, definition_revision=?, status=?,
    node_executions=?, activated_by=?, project=?,
    created_at=?, updated_at=?, completed_at=?,
    error=?, version=?
WHERE id = ? AND version = ?""",
                    (
                        *params[1:],  # skip id (it's in WHERE)
                        execution.id,
                        execution.version - 1,
                    ),
                )
                if cursor.rowcount == 0:
                    await self._db.rollback()
                    msg = (
                        f"Version conflict saving workflow execution"
                        f" {execution.id!r}: expected version"
                        f" {execution.version - 1}, not found"
                    )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                        execution_id=execution.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await self._db.commit()
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to save workflow execution {execution.id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                    execution_id=execution.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(
        self,
        execution_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        """Retrieve a workflow execution by primary key.

        Args:
            execution_id: Unique workflow execution identifier.

        Returns:
            The matching execution, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM workflow_executions WHERE id = ?",  # noqa: S608
                (execution_id,),
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = f"Failed to fetch workflow execution {execution_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_FETCH_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_WORKFLOW_EXEC_FETCHED,
                execution_id=execution_id,
                found=False,
            )
            return None

        execution = _deserialize_row(row, execution_id)
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_FETCHED,
            execution_id=execution_id,
            found=True,
        )
        return execution

    async def list_by_definition(
        self,
        definition_id: NotBlankStr,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions for a given workflow definition.

        Args:
            definition_id: The source definition identifier.

        Returns:
            Matching executions ordered by ``updated_at`` descending.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                " WHERE definition_id = ?"
                " ORDER BY updated_at DESC, id ASC LIMIT ?",
                (definition_id, _MAX_LIST_ROWS),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = f"Failed to list executions for definition {definition_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                definition_id=definition_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            _deserialize_row(row, str(dict(row).get("id", "?"))) for row in rows
        )
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_LISTED,
            definition_id=definition_id,
            count=len(executions),
        )
        return executions

    async def list_by_status(
        self,
        status: WorkflowExecutionStatus,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions with a given status.

        Args:
            status: The execution status to filter by.

        Returns:
            Matching executions ordered by ``updated_at`` descending.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                " WHERE status = ?"
                " ORDER BY updated_at DESC, id ASC LIMIT ?",
                (status.value, _MAX_LIST_ROWS),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = f"Failed to list executions with status {status.value!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                status=status.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            _deserialize_row(row, str(dict(row).get("id", "?"))) for row in rows
        )
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_LISTED,
            status=status.value,
            count=len(executions),
        )
        return executions

    async def find_by_task_id(
        self,
        task_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        """Find a RUNNING execution containing a node with the given task ID.

        Uses SQLite ``json_each()`` to search the ``node_executions``
        JSON column, filtering by RUNNING status first (leverages the
        existing status index).

        Args:
            task_id: The concrete task identifier to search for.

        Returns:
            The matching execution, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                " WHERE status = ?"
                " AND EXISTS ("
                "   SELECT 1 FROM json_each(node_executions)"
                "   WHERE json_extract(value, '$.task_id') = ?"
                " )"
                " LIMIT 1",
                (WorkflowExecutionStatus.RUNNING.value, task_id),
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = f"Failed to find execution by task_id {task_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_FIND_BY_TASK_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK,
                task_id=task_id,
                found=False,
            )
            return None

        execution = _deserialize_row(row, str(dict(row).get("id", task_id)))
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK,
            task_id=task_id,
            found=True,
            execution_id=execution.id,
        )
        return execution

    async def delete(self, execution_id: NotBlankStr) -> bool:
        """Delete a workflow execution by primary key.

        Args:
            execution_id: Unique workflow execution identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM workflow_executions WHERE id = ?",
                    (execution_id,),
                )
                await self._db.commit()
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to delete workflow execution {execution_id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED,
                    execution_id=execution_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

        return cursor.rowcount > 0
