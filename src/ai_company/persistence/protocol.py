"""PersistenceBackend protocol — lifecycle + repository access.

Application code depends on this protocol for storage lifecycle
management.  Repository protocols provide entity-level access.
"""

from typing import Protocol, runtime_checkable

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.hr.persistence_protocol import (
    CollaborationMetricRepository,  # noqa: TC001
    LifecycleEventRepository,  # noqa: TC001
    TaskMetricRepository,  # noqa: TC001
)
from ai_company.persistence.repositories import (
    CostRecordRepository,  # noqa: TC001
    MessageRepository,  # noqa: TC001
    ParkedContextRepository,  # noqa: TC001
    TaskRepository,  # noqa: TC001
)


@runtime_checkable
class PersistenceBackend(Protocol):
    """Lifecycle management for operational data storage.

    Concrete backends implement this protocol to provide connection
    management, health monitoring, schema migrations, and access to
    entity-specific repositories.

    Attributes:
        is_connected: Whether the backend has an active connection.
        backend_name: Human-readable backend identifier.
        tasks: Repository for Task persistence.
        cost_records: Repository for CostRecord persistence.
        messages: Repository for Message persistence.
        lifecycle_events: Repository for AgentLifecycleEvent persistence.
        task_metrics: Repository for TaskMetricRecord persistence.
        collaboration_metrics: Repository for CollaborationMetricRecord persistence.
    """

    async def connect(self) -> None:
        """Establish connection to the storage backend.

        Raises:
            PersistenceConnectionError: If the connection cannot be
                established.
        """
        ...

    async def disconnect(self) -> None:
        """Close the storage backend connection.

        Safe to call even if not connected.
        """
        ...

    async def health_check(self) -> bool:
        """Check whether the backend is healthy and responsive.

        Returns:
            ``True`` if the backend is reachable and operational.
        """
        ...

    async def migrate(self) -> None:
        """Run pending schema migrations.

        Raises:
            MigrationError: If a migration fails.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        ...

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier (e.g. ``"sqlite"``)."""
        ...

    @property
    def tasks(self) -> TaskRepository:
        """Repository for Task persistence."""
        ...

    @property
    def cost_records(self) -> CostRecordRepository:
        """Repository for CostRecord persistence."""
        ...

    @property
    def messages(self) -> MessageRepository:
        """Repository for Message persistence."""
        ...

    @property
    def lifecycle_events(self) -> LifecycleEventRepository:
        """Repository for AgentLifecycleEvent persistence."""
        ...

    @property
    def task_metrics(self) -> TaskMetricRepository:
        """Repository for TaskMetricRecord persistence."""
        ...

    @property
    def collaboration_metrics(self) -> CollaborationMetricRepository:
        """Repository for CollaborationMetricRecord persistence."""
        ...

    @property
    def parked_contexts(self) -> ParkedContextRepository:
        """Repository for ParkedContext persistence."""
        ...
