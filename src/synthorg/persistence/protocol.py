"""PersistenceBackend protocol -- lifecycle + repository access.

Application code depends on this protocol for storage lifecycle
management.  Repository protocols provide entity-level access.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from synthorg.api.auth.config import AuthConfig  # noqa: TC001
from synthorg.budget.config import BudgetConfig  # noqa: TC001
from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.company import Company  # noqa: TC001
from synthorg.core.role import Role  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,  # noqa: TC001
)
from synthorg.hr.evaluation.config import EvaluationConfig  # noqa: TC001
from synthorg.hr.persistence_protocol import (
    CollaborationMetricRepository,  # noqa: TC001
    LifecycleEventRepository,  # noqa: TC001
    TaskMetricRepository,  # noqa: TC001
)
from synthorg.persistence.agent_state_protocol import (
    AgentStateRepository,  # noqa: TC001
)
from synthorg.persistence.artifact_protocol import ArtifactRepository  # noqa: TC001
from synthorg.persistence.audit_protocol import AuditRepository  # noqa: TC001
from synthorg.persistence.auth_protocol import (
    LockoutRepository,  # noqa: TC001
    RefreshTokenRepository,  # noqa: TC001
    SessionRepository,  # noqa: TC001
)
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,  # noqa: TC001
    HeartbeatRepository,  # noqa: TC001
)
from synthorg.persistence.circuit_breaker_repo import (
    CircuitBreakerStateRepository,  # noqa: TC001
)
from synthorg.persistence.connection_protocol import (
    ConnectionRepository,  # noqa: TC001
    ConnectionSecretRepository,  # noqa: TC001
    OAuthStateRepository,  # noqa: TC001
    WebhookReceiptRepository,  # noqa: TC001
)
from synthorg.persistence.cost_record_protocol import (
    CostRecordRepository,  # noqa: TC001
)
from synthorg.persistence.custom_rule_repo import (
    CustomRuleRepository,  # noqa: TC001
)
from synthorg.persistence.decision_protocol import DecisionRepository  # noqa: TC001
from synthorg.persistence.escalation_protocol import (
    EscalationQueueRepository,  # noqa: TC001
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,  # noqa: TC001
    FineTuneRunRepository,  # noqa: TC001
)
from synthorg.persistence.idempotency_protocol import (
    IdempotencyRepository,  # noqa: TC001
)
from synthorg.persistence.mcp_protocol import (
    McpInstallationRepository,  # noqa: TC001
)
from synthorg.persistence.memory_protocol import (
    OrgFactRepository,  # noqa: TC001
)
from synthorg.persistence.message_protocol import MessageRepository  # noqa: TC001
from synthorg.persistence.ontology_protocol import (
    OntologyDriftReportRepository,  # noqa: TC001
    OntologyEntityRepository,  # noqa: TC001
)
from synthorg.persistence.parked_context_protocol import (
    ParkedContextRepository,  # noqa: TC001
)
from synthorg.persistence.preset_repository import (
    PersonalityPresetRepository,  # noqa: TC001
)
from synthorg.persistence.project_cost_aggregate_protocol import (
    ProjectCostAggregateRepository,  # noqa: TC001
)
from synthorg.persistence.project_protocol import ProjectRepository  # noqa: TC001
from synthorg.persistence.risk_override_repo import (
    RiskOverrideRepository,  # noqa: TC001
)
from synthorg.persistence.settings_protocol import SettingsRepository  # noqa: TC001
from synthorg.persistence.ssrf_violation_protocol import (
    SsrfViolationRepository,  # noqa: TC001
)
from synthorg.persistence.subworkflow_repo import (
    SubworkflowRepository,  # noqa: TC001
)
from synthorg.persistence.task_protocol import TaskRepository  # noqa: TC001
from synthorg.persistence.training_repos import (
    TrainingPlanRepository,  # noqa: TC001
    TrainingResultRepository,  # noqa: TC001
)
from synthorg.persistence.user_protocol import (
    ApiKeyRepository,  # noqa: TC001
    UserRepository,  # noqa: TC001
)
from synthorg.persistence.version_repo import (
    VersionRepository,  # noqa: TC001
)
from synthorg.persistence.workflow_definition_repo import (
    WorkflowDefinitionRepository,  # noqa: TC001
)
from synthorg.persistence.workflow_execution_repo import (
    WorkflowExecutionRepository,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.ontology.models import EntityDefinition
    from synthorg.versioning.service import VersioningService


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
        agent_states: Repository for AgentRuntimeState persistence.
        settings: Repository for namespaced settings persistence.
        artifacts: Repository for Artifact persistence.
        projects: Repository for Project persistence.
        custom_presets: Repository for custom personality preset persistence.
        workflow_definitions: Repository for workflow definition persistence.
        workflow_executions: Repository for workflow execution persistence.
        workflow_versions: Repository for workflow definition version
            snapshot persistence.
        identity_versions: Repository for AgentIdentity version snapshot
            persistence.
        evaluation_config_versions: Repository for EvaluationConfig version
            snapshot persistence.
        budget_config_versions: Repository for BudgetConfig version snapshot
            persistence.
        company_versions: Repository for Company version snapshot persistence.
        role_versions: Repository for Role version snapshot persistence.
        decision_records: Repository for DecisionRecord persistence
            (auditable approval-gate decisions drop-box).
        risk_overrides: Repository for RiskTierOverride persistence.
        ssrf_violations: Repository for SsrfViolation persistence.
        circuit_breaker_state: Repository for circuit breaker state
            persistence.
        connections: Repository for external service connection
            persistence.
        connection_secrets: Repository for encrypted connection secret
            persistence.
        oauth_states: Repository for transient OAuth authorization
            state persistence.
        webhook_receipts: Repository for webhook receipt log
            persistence.
        training_plans: Repository for training plan persistence.
        training_results: Repository for training result persistence.
        custom_rules: Repository for custom signal rule persistence.
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

    def get_db(self) -> Any:
        """Return the underlying database connection.

        Returns:
            The raw database connection object (backend-specific).

        Raises:
            PersistenceConnectionError: If not yet connected.
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
    def decision_records(self) -> DecisionRepository:
        """Repository for DecisionRecord persistence (decisions drop-box)."""
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

    @property
    def agent_states(self) -> AgentStateRepository:
        """Repository for AgentRuntimeState persistence."""
        ...

    @property
    def settings(self) -> SettingsRepository:
        """Repository for namespaced settings persistence."""
        ...

    @property
    def artifacts(self) -> ArtifactRepository:
        """Repository for Artifact persistence."""
        ...

    @property
    def projects(self) -> ProjectRepository:
        """Repository for Project persistence."""
        ...

    @property
    def custom_presets(self) -> PersonalityPresetRepository:
        """Repository for custom personality preset persistence."""
        ...

    @property
    def workflow_definitions(self) -> WorkflowDefinitionRepository:
        """Repository for workflow definition persistence."""
        ...

    @property
    def workflow_executions(self) -> WorkflowExecutionRepository:
        """Repository for workflow execution persistence."""
        ...

    @property
    def subworkflows(self) -> SubworkflowRepository:
        """Repository for versioned subworkflow persistence."""
        ...

    @property
    def workflow_versions(self) -> VersionRepository[WorkflowDefinition]:
        """Repository for workflow definition version snapshot persistence."""
        ...

    @property
    def identity_versions(self) -> VersionRepository[AgentIdentity]:
        """Repository for AgentIdentity version snapshot persistence."""
        ...

    @property
    def evaluation_config_versions(
        self,
    ) -> VersionRepository[EvaluationConfig]:
        """Repository for EvaluationConfig version snapshot persistence."""
        ...

    @property
    def budget_config_versions(
        self,
    ) -> VersionRepository[BudgetConfig]:
        """Repository for BudgetConfig version snapshot persistence."""
        ...

    @property
    def company_versions(self) -> VersionRepository[Company]:
        """Repository for Company version snapshot persistence."""
        ...

    @property
    def role_versions(self) -> VersionRepository[Role]:
        """Repository for Role version snapshot persistence."""
        ...

    @property
    def risk_overrides(self) -> RiskOverrideRepository:
        """Repository for risk tier override persistence."""
        ...

    @property
    def ssrf_violations(self) -> SsrfViolationRepository:
        """Repository for SSRF violation record persistence."""
        ...

    @property
    def circuit_breaker_state(self) -> CircuitBreakerStateRepository:
        """Repository for circuit breaker state persistence."""
        ...

    @property
    def connections(self) -> ConnectionRepository:
        """Repository for external service connection persistence."""
        ...

    @property
    def connection_secrets(self) -> ConnectionSecretRepository:
        """Repository for encrypted connection secret persistence."""
        ...

    @property
    def oauth_states(self) -> OAuthStateRepository:
        """Repository for transient OAuth state persistence."""
        ...

    @property
    def webhook_receipts(self) -> WebhookReceiptRepository:
        """Repository for webhook receipt log persistence."""
        ...

    @property
    def idempotency_keys(self) -> IdempotencyRepository:
        """Repository for persistent idempotency keys (#1599)."""
        ...

    @property
    def training_plans(self) -> TrainingPlanRepository:
        """Repository for training plan persistence."""
        ...

    @property
    def training_results(self) -> TrainingResultRepository:
        """Repository for training result persistence."""
        ...

    @property
    def custom_rules(self) -> CustomRuleRepository:
        """Repository for custom signal rule persistence."""
        ...

    @property
    def sessions(self) -> SessionRepository:
        """Repository for hybrid session state (durable + in-memory cache)."""
        ...

    @property
    def refresh_tokens(self) -> RefreshTokenRepository:
        """Repository for single-use refresh-token rotation."""
        ...

    @property
    def mcp_installations(self) -> McpInstallationRepository:
        """Repository for MCP catalog installation records."""
        ...

    @property
    def org_facts(self) -> OrgFactRepository:
        """Repository for organizational fact persistence (MVCC)."""
        ...

    @property
    def ontology_entities(self) -> OntologyEntityRepository:
        """Repository for ontology entity definitions."""
        ...

    @property
    def ontology_drift(self) -> OntologyDriftReportRepository:
        """Repository for ontology drift reports."""
        ...

    @property
    def project_cost_aggregates(self) -> ProjectCostAggregateRepository:
        """Repository for durable per-project cost aggregates."""
        ...

    @property
    def fine_tune_checkpoints(self) -> FineTuneCheckpointRepository:
        """Repository for fine-tune checkpoint persistence.

        Implementations that do not support fine-tuning MUST raise
        ``NotImplementedError`` with a descriptive message so callers
        do not silently receive an unusable repo.
        """
        ...

    @property
    def fine_tune_runs(self) -> FineTuneRunRepository:
        """Repository for fine-tune pipeline run persistence.

        Same availability semantics as :attr:`fine_tune_checkpoints`.
        """
        ...

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Construct a lockout repository for this backend.

        Method-based rather than property because :class:`LockoutRepository`
        needs the operator's ``AuthConfig`` (threshold, window, duration)
        which is app-layer config, not persistence-layer.  Callers supply
        the config at startup; the returned repo shares this backend's
        connection / pool.

        Raises:
            PersistenceConnectionError: If the backend is not connected.
        """
        ...

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository for this backend.

        Method-based rather than property because Postgres escalations
        accept an optional NOTIFY channel name -- cross-instance notify
        config lives on the escalation subsystem, not on persistence.
        ``notify_channel`` is ignored by the SQLite implementation.

        Raises:
            PersistenceConnectionError: If the backend is not connected.
        """
        ...

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend.

        Returns a versioning service wired to the backend's active DB
        handle.  SQLite implementations bind the service to their
        ``aiosqlite.Connection``; Postgres implementations bind to their
        ``AsyncConnectionPool``.

        Raises:
            PersistenceConnectionError: If the backend is not connected.
        """
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

        Upserts -- creates or updates the key.

        Args:
            key: Setting key.
            value: Setting value.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
