"""Repository protocols for operational data persistence.

Each entity type has its own protocol so that application code depends
only on abstract interfaces, never on a concrete backend.
"""

from typing import Protocol, runtime_checkable

from ai_company.budget.cost_record import CostRecord  # noqa: TC001
from ai_company.communication.message import Message  # noqa: TC001
from ai_company.core.enums import TaskStatus  # noqa: TC001
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.hr.persistence_protocol import (
    CollaborationMetricRepository,
    LifecycleEventRepository,
    TaskMetricRepository,
)
from ai_company.security.timeout.parked_context import ParkedContext  # noqa: TC001

__all__ = [
    "CollaborationMetricRepository",
    "CostRecordRepository",
    "LifecycleEventRepository",
    "MessageRepository",
    "ParkedContextRepository",
    "TaskMetricRepository",
    "TaskRepository",
]


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
    ) -> tuple[Task, ...]:
        """List tasks with optional filters.

        Args:
            status: Filter by task status.
            assigned_to: Filter by assignee agent ID.
            project: Filter by project ID.

        Returns:
            Matching tasks as a tuple.

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


@runtime_checkable
class CostRecordRepository(Protocol):
    """Append-only persistence + query/aggregation for CostRecord."""

    async def save(self, record: CostRecord) -> None:
        """Persist a cost record (append-only).

        Args:
            record: The cost record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> tuple[CostRecord, ...]:
        """Query cost records with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            task_id: Filter by task identifier.

        Returns:
            Matching cost records as a tuple.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def aggregate(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> float:
        """Sum total cost_usd, optionally filtered by agent and/or task.

        Args:
            agent_id: Filter by agent identifier.
            task_id: Filter by task identifier.

        Returns:
            Total cost in USD.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class MessageRepository(Protocol):
    """Write + history query interface for Message persistence."""

    async def save(self, message: Message) -> None:
        """Persist a message.

        Args:
            message: The message to persist.

        Raises:
            DuplicateRecordError: If a message with the same ID exists.
            PersistenceError: If the operation fails.
        """
        ...

    async def get_history(
        self,
        channel: NotBlankStr,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Retrieve message history for a channel.

        Args:
            channel: Channel name to query.
            limit: Maximum number of messages to return (newest first).

        Returns:
            Messages ordered by timestamp descending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class ParkedContextRepository(Protocol):
    """CRUD interface for parked agent execution contexts."""

    async def save(self, context: ParkedContext) -> None:
        """Persist a parked context.

        Args:
            context: The parked context to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, parked_id: NotBlankStr) -> ParkedContext | None:
        """Retrieve a parked context by ID.

        Args:
            parked_id: The parked context identifier.

        Returns:
            The parked context, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_approval(self, approval_id: NotBlankStr) -> ParkedContext | None:
        """Retrieve a parked context by approval ID.

        Args:
            approval_id: The approval item identifier.

        Returns:
            The parked context, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_agent(self, agent_id: NotBlankStr) -> tuple[ParkedContext, ...]:
        """Retrieve all parked contexts for an agent.

        Args:
            agent_id: The agent identifier.

        Returns:
            Parked contexts for the agent.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, parked_id: NotBlankStr) -> bool:
        """Delete a parked context by ID.

        Args:
            parked_id: The parked context identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
