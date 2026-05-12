"""Task repository protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class TaskRepository(Protocol):
    """CRUD + query interface for Task persistence."""

    async def save(self, task: Task) -> None:
        """Persist a task (insert or update).

        Args:
            task: The task to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, task_id: NotBlankStr) -> Task | None:
        """Retrieve a task by its ID.

        Args:
            task_id: The task identifier.

        Returns:
            The task, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: NotBlankStr | None = None,
        project: NotBlankStr | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks with optional filters and pagination.

        Args:
            status: Filter by task status.
            assigned_to: Filter by assignee agent ID.
            project: Filter by project ID.
            limit: Maximum rows to return.  Defaults to
                ``DEFAULT_LIST_LIMIT``; callers may pass a larger
                positive integer when they need a wider window.
            offset: Rows to skip before the window (``0`` = no offset).
                Paired with ``limit`` for cursor/offset pagination.

        Returns:
            Matching tasks as a tuple.  Ordering is deterministic on
            the primary key ``id`` (ascending) so limit/offset windows
            do not jitter across calls; the ``Task`` model has no
            ``created_at`` field so primary-key order is the only
            stable backend-agnostic signal available.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: NotBlankStr | None = None,
        project: NotBlankStr | None = None,
    ) -> int:
        """Count tasks matching the given filters.

        Args:
            status: Filter by task status.
            assigned_to: Filter by assignee agent ID.
            project: Filter by project ID.

        Returns:
            Total number of matching tasks.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, task_id: NotBlankStr) -> bool:
        """Delete a task by ID.

        Args:
            task_id: The task identifier.

        Returns:
            ``True`` if the task was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
