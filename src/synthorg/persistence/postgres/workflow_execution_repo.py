"""Postgres repository implementation for WorkflowExecution.

Postgres-native port of ``synthorg.persistence.sqlite.workflow_execution_repo``.
Uses native JSONB for ``node_executions``, and native TIMESTAMPTZ for
``created_at`` / ``updated_at`` / ``completed_at``. The protocol surface
returns the same Pydantic models as the SQLite backend.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    VersionConflictError,
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

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_SELECT_COLUMNS = """\
id, definition_id, definition_revision, status, node_executions,
activated_by, project, created_at, updated_at, completed_at,
error, version"""

_MAX_LIST_ROWS: int = 10_000
"""Safety cap on list query results pending pagination support."""


def _deserialize_node_executions(
    raw: list[Any],
) -> tuple[WorkflowNodeExecution, ...]:
    """Deserialize JSON array into WorkflowNodeExecution tuple."""
    return tuple(
        WorkflowNodeExecution(
            node_id=item["node_id"],
            node_type=WorkflowNodeType(item["node_type"]),
            status=WorkflowNodeExecutionStatus(item["status"]),
            task_id=item.get("task_id"),
            skipped_reason=item.get("skipped_reason"),
        )
        for item in raw
    )


def _deserialize_row(
    row: dict[str, Any],
    context_id: str,
) -> WorkflowExecution:
    """Reconstruct a ``WorkflowExecution`` from a Postgres dict_row.

    Postgres returns JSONB as Python list/dict (no json.loads needed),
    and TIMESTAMPTZ as timezone-aware datetime.

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
            data.get("node_executions") or [],
        )
        return WorkflowExecution.model_validate(data)
    except (
        TypeError,
        ValueError,
        ValidationError,
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


class PostgresWorkflowExecutionRepository:
    """Postgres-backed workflow execution repository.

    Provides CRUD operations for ``WorkflowExecution`` models using
    a shared ``psycopg_pool.AsyncConnectionPool``. Node executions are
    stored as JSONB. All write operations commit immediately.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, execution: WorkflowExecution) -> None:
        """Persist a workflow execution (insert or update).

        Uses explicit create/update branches rather than upsert
        to avoid version-conflict misclassification.

        Args:
            execution: Workflow execution model to persist.

        Raises:
            DuplicateRecordError: If inserting a duplicate ID.
            VersionConflictError: If optimistic concurrency check fails.
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
        node_jsonb = Jsonb(
            [ne.model_dump(mode="json") for ne in execution.node_executions],
        )
        return (
            execution.id,
            execution.definition_id,
            execution.definition_revision,
            execution.status.value,
            node_jsonb,
            execution.activated_by,
            execution.project,
            execution.created_at,
            execution.updated_at,
            execution.completed_at,
            execution.error,
            execution.version,
        )

    async def _insert(self, execution: WorkflowExecution) -> None:
        """Insert a new workflow execution row."""
        params = self._serialize_execution(execution)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO workflow_executions
                        (id, definition_id, definition_revision, status,
                         node_executions,
                         activated_by, project, created_at, updated_at, completed_at,
                         error, version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    params,
                )
                if cur.rowcount == 0:
                    msg = f"Workflow execution {execution.id!r} already exists"
                    logger.warning(
                        PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED,
                        execution_id=execution.id,
                        error=msg,
                    )
                    raise DuplicateRecordError(msg)
                await conn.commit()
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE workflow_executions SET
                        definition_id=%s, definition_revision=%s, status=%s,
                        node_executions=%s, activated_by=%s, project=%s,
                        created_at=%s, updated_at=%s, completed_at=%s,
                        error=%s, version=%s
                    WHERE id = %s AND version = %s
                    """,
                    (
                        *params[1:],  # skip id (it's in WHERE)
                        execution.id,
                        execution.version - 1,
                    ),
                )
                if cur.rowcount == 0:
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
                    raise VersionConflictError(msg)
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM workflow_executions WHERE id = %s",  # noqa: S608
                    (execution_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                    " WHERE definition_id = %s"
                    " ORDER BY updated_at DESC, id ASC LIMIT %s",
                    (definition_id, _MAX_LIST_ROWS),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list executions for definition {definition_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                definition_id=definition_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            _deserialize_row(row, str(row.get("id", "?"))) for row in rows
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                    " WHERE status = %s"
                    " ORDER BY updated_at DESC, id ASC LIMIT %s",
                    (status.value, _MAX_LIST_ROWS),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list executions with status {status.value!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED,
                status=status.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        executions = tuple(
            _deserialize_row(row, str(row.get("id", "?"))) for row in rows
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

        Uses Postgres JSONB operators to search the ``node_executions``
        column, filtering by RUNNING status first (leverages the
        existing status index).

        Args:
            task_id: The concrete task identifier to search for.

        Returns:
            The matching execution, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                task_filter = Jsonb([{"task_id": task_id}])
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM workflow_executions"  # noqa: S608
                    " WHERE status = %s"
                    " AND node_executions @> %s::jsonb"
                    " LIMIT 1",
                    (WorkflowExecutionStatus.RUNNING.value, task_filter),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

        execution = _deserialize_row(row, str(row.get("id", task_id)))
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM workflow_executions WHERE id = %s",
                    (execution_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete workflow execution {execution_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
