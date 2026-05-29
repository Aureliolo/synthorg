"""Task repository protocol."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class TaskFilterSpec(BaseModel):
    """Filter spec for ``TaskRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: TaskStatus | None = Field(
        default=None,
        description="Filter by task status",
    )
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Filter by assignee agent ID",
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Filter by project ID",
    )


@runtime_checkable
class TaskRepository(
    IdKeyedRepository[Task, NotBlankStr],
    FilteredQueryRepository[Task, TaskFilterSpec],
    Protocol,
):
    """CRUD + query interface for Task persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001).
    """

    @override
    async def save(self, entity: Task) -> None:
        """Persist a task (insert or update by id).

        Args:
            entity: The task to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> Task | None:
        """Retrieve a task by its ID.

        Args:
            entity_id: The task identifier.

        Returns:
            The task, or ``None`` if not found.

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
    ) -> tuple[Task, ...]:
        """List tasks with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Tasks ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: TaskFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks matching the filter spec.

        Args:
            filter_spec: Carries optional filters for status, assigned_to, project.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching tasks ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: TaskFilterSpec) -> int:
        """Count tasks matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching tasks.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a task by ID.

        Args:
            entity_id: The task identifier.

        Returns:
            ``True`` if the task was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
