"""HR-specific repository protocols.

Defines persistence interfaces for lifecycle events, task metrics,
and collaboration metrics.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.core.pagination import DEFAULT_LIST_LIMIT
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)


@runtime_checkable
class LifecycleEventRepository(Protocol):
    """CRUD + query interface for AgentLifecycleEvent persistence."""

    async def save(self, event: AgentLifecycleEvent) -> None:
        """Persist a lifecycle event.

        Args:
            event: The lifecycle event to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_events(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        event_type: LifecycleEventType | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[AgentLifecycleEvent, ...]:
        """List lifecycle events with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            event_type: Filter by event type.
            since: Filter events after this timestamp.
            limit: Maximum events to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching lifecycle events capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class TaskMetricRepository(Protocol):
    """Append-only persistence + query for TaskMetricRecord."""

    async def save(self, record: TaskMetricRecord) -> None:
        """Persist a task metric record.

        Args:
            record: The task metric record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query task metric records with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            since: Include records after this time.
            until: Include records before this time.
            limit: Maximum records to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching task metric records capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class CollaborationMetricRepository(Protocol):
    """Append-only persistence + query for CollaborationMetricRecord."""

    async def save(self, record: CollaborationMetricRecord) -> None:
        """Persist a collaboration metric record.

        Args:
            record: The collaboration metric record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            since: Include records after this time.
            limit: Maximum records to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching collaboration metric records capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
