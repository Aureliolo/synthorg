"""HR-specific repository protocols.

Defines persistence interfaces for lifecycle events, task metrics,
and collaboration metrics.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.enums import LifecycleEventType  # noqa: TC001
from synthorg.hr.models import AgentLifecycleEvent  # noqa: TC001
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,  # noqa: TC001
    TaskMetricRecord,  # noqa: TC001
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime


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
        since: AwareDatetime | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        """List lifecycle events with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            event_type: Filter by event type.
            since: Filter events after this timestamp.

        Returns:
            Matching lifecycle events.

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
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query task metric records with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            since: Include records after this time.
            until: Include records before this time.

        Returns:
            Matching task metric records.

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
        since: AwareDatetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Args:
            agent_id: Filter by agent identifier.
            since: Include records after this time.

        Returns:
            Matching collaboration metric records.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
