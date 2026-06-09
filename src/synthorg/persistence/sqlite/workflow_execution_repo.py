# module-kind: repository
"""SQLite repository implementation for WorkflowExecution.

Node executions are stored as a JSON array and timestamps as ISO TEXT.
Row <-> model marshalling is shared with the Postgres sibling via
:mod:`synthorg.persistence._shared.workflow_execution_marshalling`.
"""

import json
import sqlite3
from datetime import UTC

import aiosqlite

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import WorkflowExecutionStatus
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.workflow_exec import (
    PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FETCH_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FETCHED,
    PERSISTENCE_WORKFLOW_EXEC_FIND_BY_TASK_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK,
    PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
    PERSISTENCE_WORKFLOW_EXEC_LISTED,
    PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence._shared.workflow_execution_marshalling import (
    WORKFLOW_EXECUTION_COLUMNS,
    build_workflow_execution_where,
    node_execution_payloads,
    row_to_workflow_execution,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
)

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000
"""Safety cap on list query results pending pagination support."""


class SQLiteWorkflowExecutionRepository:
    """SQLite-backed workflow execution repository.

    Provides CRUD operations for ``WorkflowExecution`` models using
    a shared ``aiosqlite.Connection``.  Node executions are stored
    as a JSON array.  All write operations commit immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
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

    async def save(self, execution: WorkflowExecution) -> None:
        """Persist a workflow execution (insert or update).

        Uses explicit create/update branches rather than upsert
        to avoid version-conflict misclassification.

        Args:
            execution: Workflow execution model to persist.

        Raises:
            DuplicateRecordError: If inserting a duplicate ID.
            PersistenceVersionConflictError: If the row exists but its
                stored version differs from ``execution.version - 1``.
            RecordNotFoundError: If updating a row that no longer
                exists (delete race between read and update).
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
        """Build the parameter tuple for insert/update SQL.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        node_json = json.dumps(node_execution_payloads(execution))
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
        """Insert a new workflow execution row.

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        params = self._serialize_execution(execution)
        async with self._write_context():
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
        """Update an existing workflow execution with version check.

        Raises:
            PersistenceVersionConflictError: If the row version no longer matches.
            QueryError: If the database query fails.
            RecordNotFoundError: If no row matches the supplied key.
        """
        params = self._serialize_execution(execution)
        async with self._write_context():
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
                    probe = await self._db.execute(
                        "SELECT version FROM workflow_executions WHERE id = ?",
                        (execution.id,),
                    )
                    row = await probe.fetchone()
                    await self._db.rollback()
                    if row is None:
                        msg = (
                            f"Workflow execution {execution.id!r} not found"
                            f" (deleted between read and update)"
                        )
                        logger.warning(
                            PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                            execution_id=execution.id,
                            error=msg,
                        )
                        raise RecordNotFoundError(msg)
                    msg = (
                        f"Version conflict saving workflow execution"
                        f" {execution.id!r}: expected version"
                        f" {execution.version - 1}, current is {row[0]}"
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
                f"SELECT {WORKFLOW_EXECUTION_COLUMNS} FROM workflow_executions WHERE id = ?",  # noqa: S608, E501
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

        execution = row_to_workflow_execution(dict(row), execution_id)
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_FETCHED,
            execution_id=execution_id,
            found=True,
        )
        return execution

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        """List all executions with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Executions ordered by id ascending.

        Raises:
            QueryError: If the database query or pagination validation
                fails.
        """
        limit = validate_pagination_args(
            limit, offset=offset, event=PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            cursor = await self._db.execute(
                f"SELECT {WORKFLOW_EXECUTION_COLUMNS} FROM workflow_executions"  # noqa: S608
                " ORDER BY id ASC LIMIT ? OFFSET ?",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list executions"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            row_to_workflow_execution(dict(row), str(dict(row).get("id", "?")))
            for row in rows
        )
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_LISTED,
            count=len(executions),
        )
        return executions

    async def query(
        self,
        filter_spec: WorkflowExecutionFilterSpec,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions matching the filter spec.

        Args:
            filter_spec: Carries optional filters for definition_id and
                status.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching executions ordered by updated_at descending, then id
            ascending.

        Raises:
            QueryError: If the database query or pagination validation
                fails.
        """
        limit = validate_pagination_args(
            limit, offset=offset, event=PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        where_clause, params = build_workflow_execution_where(
            filter_spec, placeholder="?"
        )
        params.extend([effective_limit, offset])

        try:
            cursor = await self._db.execute(
                f"SELECT {WORKFLOW_EXECUTION_COLUMNS} FROM workflow_executions"  # noqa: S608
                f" WHERE {where_clause}"
                " ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?",
                params,
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to query executions"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                definition_id=filter_spec.definition_id,
                status=filter_spec.status.value if filter_spec.status else None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            row_to_workflow_execution(dict(row), str(dict(row).get("id", "?")))
            for row in rows
        )
        logger.debug(
            PERSISTENCE_WORKFLOW_EXEC_LISTED,
            definition_id=filter_spec.definition_id,
            status=filter_spec.status.value if filter_spec.status else None,
            count=len(executions),
        )
        return executions

    async def count(self, filter_spec: WorkflowExecutionFilterSpec) -> int:
        """Count executions matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching executions.

        Raises:
            QueryError: If the database query fails.
        """
        where_clause, params = build_workflow_execution_where(
            filter_spec, placeholder="?"
        )
        try:
            cursor = await self._db.execute(
                f"SELECT COUNT(*) FROM workflow_executions"  # noqa: S608
                f" WHERE {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.Error as exc:
            msg = "Failed to count executions"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                definition_id=filter_spec.definition_id,
                status=filter_spec.status.value if filter_spec.status else None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
                f"SELECT {WORKFLOW_EXECUTION_COLUMNS} FROM workflow_executions"  # noqa: S608
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

        row_data = dict(row)
        execution = row_to_workflow_execution(
            row_data, str(row_data.get("id", task_id))
        )
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
        async with self._write_context():
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


__all__ = ["SQLiteWorkflowExecutionRepository"]
