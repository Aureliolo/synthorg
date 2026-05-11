"""Repository protocol for workflow execution persistence."""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import WorkflowExecutionStatus  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,  # noqa: TC001
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class WorkflowExecutionRepository(Protocol):
    """CRUD interface for workflow execution persistence.

    Workflow executions are runtime instances of activated
    workflow definitions, tracking per-node execution state
    and mapping to concrete tasks.
    """

    async def save(self, execution: WorkflowExecution) -> None:
        """Persist a workflow execution (insert or update).

        Args:
            execution: The workflow execution to persist.

        Raises:
            DuplicateRecordError: If inserting a duplicate ID.
            PersistenceVersionConflictError: If the row exists but its
                stored version differs from ``execution.version - 1``.
            RecordNotFoundError: If updating a row that no longer
                exists (delete race between read and update).
            PersistenceError: If the operation fails.
        """
        ...

    async def get(
        self,
        execution_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        """Retrieve a workflow execution by its ID.

        Args:
            execution_id: The execution identifier.

        Returns:
            The execution, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_by_definition(
        self,
        definition_id: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions for a given workflow definition.

        Args:
            definition_id: The source definition identifier.
            limit: Maximum executions to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching executions as a tuple, ordered by
            ``updated_at`` descending, capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_by_status(
        self,
        status: WorkflowExecutionStatus,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions with a given status.

        Args:
            status: The execution status to filter by.
            limit: Maximum executions to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching executions as a tuple, ordered by
            ``updated_at`` descending, capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def find_by_task_id(
        self,
        task_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        """Find a RUNNING execution containing a node with the given task ID.

        Args:
            task_id: The concrete task identifier to search for.

        Returns:
            The matching execution, or ``None`` if no running
            execution contains this task ID.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def delete(self, execution_id: NotBlankStr) -> bool:
        """Delete a workflow execution by ID.

        Args:
            execution_id: The execution identifier.

        Returns:
            ``True`` if the execution was deleted,
            ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
