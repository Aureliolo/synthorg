"""Repository protocol for workflow execution persistence."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import WorkflowExecutionStatus
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
)
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class WorkflowExecutionFilterSpec(BaseModel):
    """Filter spec for ``WorkflowExecutionRepository.query``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    definition_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by workflow definition ID",
    )
    status: WorkflowExecutionStatus | None = Field(
        default=None,
        description="Filter by execution status",
    )


@runtime_checkable
class WorkflowExecutionRepository(
    IdKeyedRepository[WorkflowExecution, NotBlankStr],
    FilteredQueryRepository[WorkflowExecution, "WorkflowExecutionFilterSpec"],
    Protocol,
):
    """CRUD + query interface for workflow execution persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Entity is keyed by ``id`` field.

    **Note on save semantics**: The :meth:`save` method retains optimistic-
    concurrency checking (``PersistenceVersionConflictError`` on version
    mismatch), which differs from the typical idempotent upsert contract.
    Callers must respect the version increment and handle conflicts
    explicitly; this divergence is documented here because it is domain-
    driven (workflow execution state machines require atomic transitions)
    and permanent.

    **Bespoke method per ADR-0001 D7**: :meth:`find_by_task_id` encodes an
    alternate-key lookup (JSON node array probe) not expressible via the
    generic filter interface. This is a real domain invariant and
    perf-sensitive operation.
    """

    @override
    async def save(self, execution: WorkflowExecution) -> None:
        """Persist a workflow execution (insert or update).

        Optimistic concurrency: if the execution's version does not match
        the stored row's version + 1, raises
        ``PersistenceVersionConflictError``.

        Args:
            execution: The workflow execution to persist.

        Raises:
            DuplicateRecordError: If inserting a duplicate ID.
            PersistenceVersionConflictError: If the row exists but its
                stored version does not match execution.version - 1.
            RecordNotFoundError: If updating a row that no longer exists
                (delete race between read and update).
            PersistenceError: If the operation fails.
        """
        ...

    @override
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

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        """List all workflow executions with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Executions ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: WorkflowExecutionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
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
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: WorkflowExecutionFilterSpec) -> int:
        """Count executions matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching executions.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def find_by_task_id(
        self,
        task_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        """Find a RUNNING execution containing a node with the given task ID.

        This is a bespoke alternate-key lookup via JSON probe,
        not expressible via a generic filter interface.

        Args:
            task_id: The concrete task identifier to search for.

        Returns:
            The matching execution, or ``None`` if no running execution
            contains this task ID.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
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
