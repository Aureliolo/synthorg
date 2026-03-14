"""PersistenceBackend protocol — lifecycle + repository access.

Application code depends on this protocol for storage lifecycle
management.  Repository protocols provide entity-level access.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.persistence_protocol import (
    CollaborationMetricRepository,  # noqa: TC001
    LifecycleEventRepository,  # noqa: TC001
    TaskMetricRepository,  # noqa: TC001
)
from synthorg.persistence.repositories import (
    ApiKeyRepository,  # noqa: TC001
    AuditRepository,  # noqa: TC001
    CheckpointRepository,  # noqa: TC001
    CostRecordRepository,  # noqa: TC001
    HeartbeatRepository,  # noqa: TC001
    MessageRepository,  # noqa: TC001
    ParkedContextRepository,  # noqa: TC001
    TaskRepository,  # noqa: TC001
    UserRepository,  # noqa: TC001
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
        parked_contexts: Repository for ParkedContext persistence.
        audit_entries: Repository for AuditEntry persistence.
        users: Repository for User persistence.
        api_keys: Repository for ApiKey persistence.
        checkpoints: Repository for Checkpoint persistence.
        heartbeats: Repository for Heartbeat persistence.
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

    @property
    def audit_entries(self) -> AuditRepository:
        """Repository for AuditEntry persistence."""
        ...

    @property
    def users(self) -> UserRepository:
        """Repository for User persistence."""
        ...

    @property
    def api_keys(self) -> ApiKeyRepository:
        """Repository for ApiKey persistence."""
        ...

    @property
    def checkpoints(self) -> CheckpointRepository:
        """Repository for Checkpoint persistence."""
        ...

    @property
    def heartbeats(self) -> HeartbeatRepository:
        """Repository for Heartbeat persistence."""
        ...

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key.

        Args:
            key: Setting key.

        Returns:
            The setting value, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value.

        Upserts — creates or updates the key.

        Args:
            key: Setting key.
            value: Setting value.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
