"""Postgres persistence backend implementation.

Implements the ``PersistenceBackend`` protocol on top of psycopg 3 and
``psycopg_pool.AsyncConnectionPool``.  Repositories are instantiated
per-backend on ``connect()`` and receive the shared pool; each pool
checkout is an independent transaction, so the Postgres backend does
not need the ``shared_write_lock`` workaround that the SQLite backend
uses to serialize writes across a single in-process connection.

The schema uses native Postgres types (JSONB, TIMESTAMPTZ, BIGINT,
BOOLEAN) -- see ``src/synthorg/persistence/postgres/schema.sql``.  At
the Python level, the protocol surface is identical to the SQLite
backend: callers get Pydantic models back either way.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from psycopg.rows import dict_row
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_NOT_CONNECTED,
)
from synthorg.persistence.config import PostgresConfig  # noqa: TC001
from synthorg.persistence.errors import PersistenceConnectionError
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,  # noqa: TC001
    FineTuneRunRepository,  # noqa: TC001
)
from synthorg.persistence.integration_stubs import (
    InMemoryConnectionRepository,
    InMemoryConnectionSecretRepository,
    InMemoryOAuthStateRepository,
    InMemoryWebhookReceiptRepository,
)
from synthorg.persistence.postgres.agent_state_repo import (
    PostgresAgentStateRepository,
)
from synthorg.persistence.postgres.artifact_repo import PostgresArtifactRepository
from synthorg.persistence.postgres.audit_repository import PostgresAuditRepository
from synthorg.persistence.postgres.backend_connection import PostgresConnectionMixin
from synthorg.persistence.postgres.backend_migration import PostgresMigrationMixin
from synthorg.persistence.postgres.checkpoint_repo import (
    PostgresCheckpointRepository,
)
from synthorg.persistence.postgres.circuit_breaker_repo import (
    PostgresCircuitBreakerStateRepository,
)
from synthorg.persistence.postgres.custom_rule_repo import (
    PostgresCustomRuleRepository,
)
from synthorg.persistence.postgres.decision_repo import PostgresDecisionRepository
from synthorg.persistence.postgres.fine_tune_repo import (
    PostgresFineTuneCheckpointRepository,
    PostgresFineTuneRunRepository,
)
from synthorg.persistence.postgres.heartbeat_repo import (
    PostgresHeartbeatRepository,
)
from synthorg.persistence.postgres.hr_repositories import (
    PostgresCollaborationMetricRepository,
    PostgresLifecycleEventRepository,
    PostgresTaskMetricRepository,
)
from synthorg.persistence.postgres.idempotency_repo import (
    PostgresIdempotencyRepository,
)
from synthorg.persistence.postgres.lockout_repo import (
    PostgresLockoutRepository,
)
from synthorg.persistence.postgres.mcp_installation_repo import (
    PostgresMcpInstallationRepository,
)
from synthorg.persistence.postgres.ontology_drift_repo import (
    PostgresOntologyDriftReportRepository,
)
from synthorg.persistence.postgres.ontology_entity_repo import (
    PostgresOntologyEntityRepository,
)
from synthorg.persistence.postgres.org_fact_repo import (
    PostgresOrgFactRepository,
)
from synthorg.persistence.postgres.parked_context_repo import (
    PostgresParkedContextRepository,
)
from synthorg.persistence.postgres.preset_repo import (
    PostgresPersonalityPresetRepository,
)
from synthorg.persistence.postgres.project_cost_aggregate_repo import (
    PostgresProjectCostAggregateRepository,
)
from synthorg.persistence.postgres.project_repo import PostgresProjectRepository
from synthorg.persistence.postgres.refresh_repo import (
    PostgresRefreshTokenRepository,
)
from synthorg.persistence.postgres.repositories import (
    PostgresCostRecordRepository,
    PostgresMessageRepository,
    PostgresTaskRepository,
)
from synthorg.persistence.postgres.risk_override_repo import (
    PostgresRiskOverrideRepository,
)
from synthorg.persistence.postgres.session_repo import (
    PostgresSessionRepository,
)
from synthorg.persistence.postgres.settings_repo import PostgresSettingsRepository
from synthorg.persistence.postgres.ssrf_violation_repo import (
    PostgresSsrfViolationRepository,
)
from synthorg.persistence.postgres.subworkflow_repo import (
    PostgresSubworkflowRepository,
)
from synthorg.persistence.postgres.training_plan_repo import (
    PostgresTrainingPlanRepository,
)
from synthorg.persistence.postgres.training_result_repo import (
    PostgresTrainingResultRepository,
)
from synthorg.persistence.postgres.user_repo import (
    PostgresApiKeyRepository,
    PostgresUserRepository,
)
from synthorg.persistence.postgres.version_repo import PostgresVersionRepository
from synthorg.persistence.postgres.workflow_definition_repo import (
    PostgresWorkflowDefinitionRepository,
)
from synthorg.persistence.postgres.workflow_execution_repo import (
    PostgresWorkflowExecutionRepository,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.api.auth.config import AuthConfig
    from synthorg.hr.persistence_protocol import (
        CollaborationMetricRepository,
        LifecycleEventRepository,
        TaskMetricRepository,
    )
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.agent_state_protocol import AgentStateRepository
    from synthorg.persistence.artifact_protocol import ArtifactRepository
    from synthorg.persistence.audit_protocol import AuditRepository
    from synthorg.persistence.auth_protocol import LockoutRepository
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.circuit_breaker_repo import (
        CircuitBreakerStateRepository,
    )
    from synthorg.persistence.cost_record_protocol import CostRecordRepository
    from synthorg.persistence.decision_protocol import DecisionRepository
    from synthorg.persistence.escalation_protocol import EscalationQueueRepository
    from synthorg.persistence.message_protocol import MessageRepository
    from synthorg.persistence.parked_context_protocol import (
        ParkedContextRepository,
    )
    from synthorg.persistence.preset_repository import PersonalityPresetRepository
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.persistence.risk_override_repo import RiskOverrideRepository
    from synthorg.persistence.settings_protocol import SettingsRepository
    from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository
    from synthorg.persistence.subworkflow_repo import SubworkflowRepository
    from synthorg.persistence.task_protocol import TaskRepository
    from synthorg.persistence.user_protocol import (
        ApiKeyRepository,
        UserRepository,
    )
    from synthorg.persistence.version_repo import VersionRepository
    from synthorg.persistence.workflow_definition_repo import (
        WorkflowDefinitionRepository,
    )
    from synthorg.persistence.workflow_execution_repo import (
        WorkflowExecutionRepository,
    )
    from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class PostgresPersistenceBackend(PostgresConnectionMixin, PostgresMigrationMixin):
    """Postgres implementation of the ``PersistenceBackend`` protocol.

    Uses a ``psycopg_pool.AsyncConnectionPool`` for connection
    management.  Each repository method acquires a connection from the
    pool for the duration of its critical section, so writes are
    isolated per-connection transaction.  There is no shared write
    lock -- unlike SQLite, Postgres per-connection transactions do not
    share a single in-process connection.

    Args:
        config: Postgres-specific configuration.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._lifecycle_lock = asyncio.Lock()
        self._pool: AsyncConnectionPool | None = None
        # Repository attributes -- instantiated in Phase 3 ports.
        self._artifacts: ArtifactRepository | None = None
        self._projects: ProjectRepository | None = None
        self._tasks: TaskRepository | None = None
        self._cost_records: CostRecordRepository | None = None
        self._messages: MessageRepository | None = None
        self._lifecycle_events: LifecycleEventRepository | None = None
        self._task_metrics: TaskMetricRepository | None = None
        self._collaboration_metrics: CollaborationMetricRepository | None = None
        self._parked_contexts: ParkedContextRepository | None = None
        self._audit_entries: AuditRepository | None = None
        self._users: UserRepository | None = None
        self._api_keys: ApiKeyRepository | None = None
        self._checkpoints: CheckpointRepository | None = None
        self._heartbeats: HeartbeatRepository | None = None
        self._agent_states: AgentStateRepository | None = None
        self._settings: SettingsRepository | None = None
        self._custom_presets: PersonalityPresetRepository | None = None
        self._workflow_definitions: WorkflowDefinitionRepository | None = None
        self._workflow_executions: WorkflowExecutionRepository | None = None
        self._subworkflows: SubworkflowRepository | None = None
        self._workflow_versions: VersionRepository[WorkflowDefinition] | None = None
        self._identity_versions: VersionRepository[AgentIdentity] | None = None
        self._evaluation_config_versions: VersionRepository[EvaluationConfig] | None = (
            None
        )
        self._budget_config_versions: VersionRepository[BudgetConfig] | None = None
        self._company_versions: VersionRepository[Company] | None = None
        self._role_versions: VersionRepository[Role] | None = None
        self._decision_records: DecisionRepository | None = None
        self._risk_overrides: RiskOverrideRepository | None = None
        self._ssrf_violations: SsrfViolationRepository | None = None
        self._circuit_breaker_state: CircuitBreakerStateRepository | None = None
        self._training_plans: PostgresTrainingPlanRepository | None = None
        self._training_results: PostgresTrainingResultRepository | None = None
        self._sessions: PostgresSessionRepository | None = None
        self._refresh_tokens: PostgresRefreshTokenRepository | None = None
        self._idempotency_keys: PostgresIdempotencyRepository | None = None
        self._mcp_installations: PostgresMcpInstallationRepository | None = None
        self._custom_rules: PostgresCustomRuleRepository | None = None
        self._org_facts: PostgresOrgFactRepository | None = None
        self._ontology_entities: PostgresOntologyEntityRepository | None = None
        self._ontology_drift: PostgresOntologyDriftReportRepository | None = None
        self._connections_stub = InMemoryConnectionRepository()
        self._connection_secrets_stub = InMemoryConnectionSecretRepository()
        self._oauth_states_stub = InMemoryOAuthStateRepository()
        self._webhook_receipts_stub = InMemoryWebhookReceiptRepository()
        self._project_cost_aggregates: PostgresProjectCostAggregateRepository | None = (
            None
        )
        self._fine_tune_runs: PostgresFineTuneRunRepository | None = None
        self._fine_tune_checkpoints: PostgresFineTuneCheckpointRepository | None = None

    def _clear_state(self) -> None:
        """Reset pool and repository references to ``None``."""
        self._pool = None
        self._artifacts = None
        self._projects = None
        self._tasks = None
        self._cost_records = None
        self._messages = None
        self._lifecycle_events = None
        self._task_metrics = None
        self._collaboration_metrics = None
        self._parked_contexts = None
        self._audit_entries = None
        self._users = None
        self._api_keys = None
        self._checkpoints = None
        self._heartbeats = None
        self._agent_states = None
        self._settings = None
        self._custom_presets = None
        self._workflow_definitions = None
        self._workflow_executions = None
        self._subworkflows = None
        self._workflow_versions = None
        self._identity_versions = None
        self._evaluation_config_versions = None
        self._budget_config_versions = None
        self._company_versions = None
        self._role_versions = None
        self._decision_records = None
        self._risk_overrides = None
        self._ssrf_violations = None
        self._circuit_breaker_state = None
        self._project_cost_aggregates = None
        self._training_plans = None
        self._training_results = None
        self._sessions = None
        self._refresh_tokens = None
        self._mcp_installations = None
        self._custom_rules = None
        self._org_facts = None
        self._ontology_entities = None
        self._ontology_drift = None
        self._fine_tune_runs = None
        self._fine_tune_checkpoints = None

    def _create_repositories(self) -> None:
        """Instantiate all repository objects from the active pool."""
        assert self._pool is not None  # noqa: S101
        pool = self._pool

        # Core domain repositories.
        self._artifacts = PostgresArtifactRepository(pool)
        self._projects = PostgresProjectRepository(pool)
        self._tasks = PostgresTaskRepository(pool)
        self._cost_records = PostgresCostRecordRepository(pool)
        self._messages = PostgresMessageRepository(pool)

        # HR repositories.
        self._lifecycle_events = PostgresLifecycleEventRepository(pool)
        self._task_metrics = PostgresTaskMetricRepository(pool)
        self._collaboration_metrics = PostgresCollaborationMetricRepository(pool)

        # Operational + security repositories.
        self._parked_contexts = PostgresParkedContextRepository(pool)
        self._audit_entries = PostgresAuditRepository(pool)
        self._users = PostgresUserRepository(pool)
        self._api_keys = PostgresApiKeyRepository(pool)
        self._checkpoints = PostgresCheckpointRepository(pool)
        self._heartbeats = PostgresHeartbeatRepository(pool)
        self._agent_states = PostgresAgentStateRepository(pool)
        self._settings = PostgresSettingsRepository(pool)
        self._custom_presets = PostgresPersonalityPresetRepository(pool)

        # Workflow repositories.
        self._workflow_definitions = PostgresWorkflowDefinitionRepository(pool)
        self._workflow_executions = PostgresWorkflowExecutionRepository(pool)
        self._subworkflows = PostgresSubworkflowRepository(pool)

        # Generic version repositories (one per versioned entity type).
        def _ver_repo[T: BaseModel](
            table: str,
            model_cls: type[T],
        ) -> PostgresVersionRepository[T]:
            def _deserialize(d: object) -> T:
                return model_cls.model_validate(d)

            return PostgresVersionRepository(
                pool=pool,
                table_name=NotBlankStr(table),
                serialize_snapshot=lambda m: m.model_dump(mode="json"),
                deserialize_snapshot=_deserialize,
            )

        self._workflow_versions = _ver_repo(
            "workflow_definition_versions", WorkflowDefinition
        )
        self._identity_versions = _ver_repo("agent_identity_versions", AgentIdentity)
        self._evaluation_config_versions = _ver_repo(
            "evaluation_config_versions", EvaluationConfig
        )
        self._budget_config_versions = _ver_repo("budget_config_versions", BudgetConfig)
        self._company_versions = _ver_repo("company_versions", Company)
        self._role_versions = _ver_repo("role_versions", Role)

        # Append-only / security repositories.  Postgres per-connection
        # transactions handle isolation without the SQLite shared
        # write_lock workaround.
        self._decision_records = PostgresDecisionRepository(pool)
        self._risk_overrides = PostgresRiskOverrideRepository(pool)
        self._ssrf_violations = PostgresSsrfViolationRepository(pool)
        self._circuit_breaker_state = PostgresCircuitBreakerStateRepository(pool)
        self._project_cost_aggregates = PostgresProjectCostAggregateRepository(pool)
        self._training_plans = PostgresTrainingPlanRepository(pool)
        self._training_results = PostgresTrainingResultRepository(pool)
        self._sessions = PostgresSessionRepository(pool)
        self._refresh_tokens = PostgresRefreshTokenRepository(pool)
        self._idempotency_keys = PostgresIdempotencyRepository(pool)
        self._mcp_installations = PostgresMcpInstallationRepository(pool)
        self._custom_rules = PostgresCustomRuleRepository(pool)
        self._org_facts = PostgresOrgFactRepository(pool)
        self._ontology_entities = PostgresOntologyEntityRepository(pool)
        self._ontology_drift = PostgresOntologyDriftReportRepository(pool)
        self._fine_tune_runs = PostgresFineTuneRunRepository(pool)
        self._fine_tune_checkpoints = PostgresFineTuneCheckpointRepository(pool)

    def get_db(self) -> AsyncConnectionPool:
        """Return the shared connection pool.

        Raises:
            PersistenceConnectionError: If not yet connected.
        """
        if self._pool is None:
            msg = "Postgres backend not connected"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return self._pool

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an open pool."""
        return self._pool is not None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("postgres")

    def _require_connected[T](self, repo: T | None, name: str) -> T:
        """Return *repo* or raise if the backend is not connected.

        Args:
            repo: Repository instance (``None`` when disconnected or
                not yet ported).
            name: Repository name for the error message.

        Raises:
            PersistenceConnectionError: If *repo* is ``None``.
        """
        if repo is None:
            if self._pool is None:
                msg = f"Not connected -- call connect() before accessing {name}"
            else:
                msg = (
                    f"Postgres {name} repository is not yet implemented "
                    f"(Phase 3 port pending)"
                )
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return repo

    @property
    def tasks(self) -> TaskRepository:
        """Repository for Task persistence."""
        return self._require_connected(self._tasks, "tasks")

    @property
    def cost_records(self) -> CostRecordRepository:
        """Repository for CostRecord persistence."""
        return self._require_connected(self._cost_records, "cost_records")

    @property
    def messages(self) -> MessageRepository:
        """Repository for Message persistence."""
        return self._require_connected(self._messages, "messages")

    @property
    def lifecycle_events(self) -> LifecycleEventRepository:
        """Repository for AgentLifecycleEvent persistence."""
        return self._require_connected(self._lifecycle_events, "lifecycle_events")

    @property
    def task_metrics(self) -> TaskMetricRepository:
        """Repository for TaskMetricRecord persistence."""
        return self._require_connected(self._task_metrics, "task_metrics")

    @property
    def collaboration_metrics(self) -> CollaborationMetricRepository:
        """Repository for CollaborationMetricRecord persistence."""
        return self._require_connected(
            self._collaboration_metrics, "collaboration_metrics"
        )

    @property
    def parked_contexts(self) -> ParkedContextRepository:
        """Repository for ParkedContext persistence."""
        return self._require_connected(self._parked_contexts, "parked_contexts")

    @property
    def audit_entries(self) -> AuditRepository:
        """Repository for AuditEntry persistence."""
        return self._require_connected(self._audit_entries, "audit_entries")

    @property
    def decision_records(self) -> DecisionRepository:
        """Repository for DecisionRecord persistence."""
        return self._require_connected(self._decision_records, "decision_records")

    @property
    def users(self) -> UserRepository:
        """Repository for User persistence."""
        return self._require_connected(self._users, "users")

    @property
    def api_keys(self) -> ApiKeyRepository:
        """Repository for ApiKey persistence."""
        return self._require_connected(self._api_keys, "api_keys")

    @property
    def checkpoints(self) -> CheckpointRepository:
        """Repository for Checkpoint persistence."""
        return self._require_connected(self._checkpoints, "checkpoints")

    @property
    def heartbeats(self) -> HeartbeatRepository:
        """Repository for Heartbeat persistence."""
        return self._require_connected(self._heartbeats, "heartbeats")

    @property
    def agent_states(self) -> AgentStateRepository:
        """Repository for AgentRuntimeState persistence."""
        return self._require_connected(self._agent_states, "agent_states")

    @property
    def settings(self) -> SettingsRepository:
        """Repository for namespaced settings persistence."""
        return self._require_connected(self._settings, "settings")

    @property
    def artifacts(self) -> ArtifactRepository:
        """Repository for Artifact persistence."""
        return self._require_connected(self._artifacts, "artifacts")

    @property
    def projects(self) -> ProjectRepository:
        """Repository for Project persistence."""
        return self._require_connected(self._projects, "projects")

    @property
    def custom_presets(self) -> PersonalityPresetRepository:
        """Repository for custom personality preset persistence."""
        return self._require_connected(self._custom_presets, "custom_presets")

    @property
    def workflow_definitions(self) -> WorkflowDefinitionRepository:
        """Repository for workflow definition persistence."""
        return self._require_connected(
            self._workflow_definitions, "workflow_definitions"
        )

    @property
    def workflow_executions(self) -> WorkflowExecutionRepository:
        """Repository for workflow execution persistence."""
        return self._require_connected(self._workflow_executions, "workflow_executions")

    @property
    def subworkflows(self) -> SubworkflowRepository:
        """Repository for subworkflow registry persistence."""
        return self._require_connected(self._subworkflows, "subworkflows")

    @property
    def workflow_versions(self) -> VersionRepository[WorkflowDefinition]:
        """Repository for workflow definition version persistence."""
        return self._require_connected(self._workflow_versions, "workflow_versions")

    @property
    def identity_versions(self) -> VersionRepository[AgentIdentity]:
        """Repository for AgentIdentity version snapshot persistence."""
        return self._require_connected(self._identity_versions, "identity_versions")

    @property
    def evaluation_config_versions(
        self,
    ) -> VersionRepository[EvaluationConfig]:
        """Repository for EvaluationConfig version snapshot persistence."""
        return self._require_connected(
            self._evaluation_config_versions, "evaluation_config_versions"
        )

    @property
    def budget_config_versions(self) -> VersionRepository[BudgetConfig]:
        """Repository for BudgetConfig version snapshot persistence."""
        return self._require_connected(
            self._budget_config_versions, "budget_config_versions"
        )

    @property
    def company_versions(self) -> VersionRepository[Company]:
        """Repository for Company version snapshot persistence."""
        return self._require_connected(self._company_versions, "company_versions")

    @property
    def role_versions(self) -> VersionRepository[Role]:
        """Repository for Role version snapshot persistence."""
        return self._require_connected(self._role_versions, "role_versions")

    @property
    def risk_overrides(self) -> RiskOverrideRepository:
        """Repository for risk tier override persistence."""
        return self._require_connected(self._risk_overrides, "risk_overrides")

    @property
    def ssrf_violations(self) -> SsrfViolationRepository:
        """Repository for SSRF violation record persistence."""
        return self._require_connected(self._ssrf_violations, "ssrf_violations")

    @property
    def circuit_breaker_state(self) -> CircuitBreakerStateRepository:
        """Repository for circuit breaker state persistence."""
        return self._require_connected(
            self._circuit_breaker_state, "circuit_breaker_state"
        )

    @property
    def project_cost_aggregates(self) -> PostgresProjectCostAggregateRepository:
        """Repository for durable project cost aggregates.

        Raises:
            PersistenceConnectionError: If not connected.
        """
        return self._require_connected(
            self._project_cost_aggregates,
            "project_cost_aggregates",
        )

    @property
    def fine_tune_checkpoints(self) -> FineTuneCheckpointRepository:
        """Repository for fine-tune checkpoint persistence."""
        return self._require_connected(
            self._fine_tune_checkpoints,
            "fine_tune_checkpoints",
        )

    @property
    def fine_tune_runs(self) -> FineTuneRunRepository:
        """Repository for fine-tune pipeline runs."""
        return self._require_connected(self._fine_tune_runs, "fine_tune_runs")

    @property
    def connections(self) -> InMemoryConnectionRepository:
        """Repository for external service connection persistence."""
        return self._connections_stub

    @property
    def connection_secrets(self) -> InMemoryConnectionSecretRepository:
        """Repository for encrypted connection secret persistence."""
        return self._connection_secrets_stub

    @property
    def oauth_states(self) -> InMemoryOAuthStateRepository:
        """Repository for transient OAuth state persistence."""
        return self._oauth_states_stub

    @property
    def webhook_receipts(self) -> InMemoryWebhookReceiptRepository:
        """Repository for webhook receipt log persistence."""
        return self._webhook_receipts_stub

    @property
    def training_plans(self) -> PostgresTrainingPlanRepository:
        """Repository for training plan persistence."""
        return self._require_connected(
            self._training_plans,
            "training_plans",
        )

    @property
    def training_results(self) -> PostgresTrainingResultRepository:
        """Repository for training result persistence."""
        return self._require_connected(
            self._training_results,
            "training_results",
        )

    @property
    def custom_rules(self) -> PostgresCustomRuleRepository:
        """Repository for custom signal rule persistence."""
        return self._require_connected(self._custom_rules, "custom_rules")

    @property
    def sessions(self) -> PostgresSessionRepository:
        """Repository for hybrid session state (durable + in-memory cache)."""
        return self._require_connected(self._sessions, "sessions")

    @property
    def refresh_tokens(self) -> PostgresRefreshTokenRepository:
        """Repository for single-use refresh-token rotation."""
        return self._require_connected(
            self._refresh_tokens,
            "refresh_tokens",
        )

    @property
    def idempotency_keys(self) -> PostgresIdempotencyRepository:
        """Repository for persistent idempotency keys (#1599)."""
        return self._require_connected(
            self._idempotency_keys,
            "idempotency_keys",
        )

    @property
    def mcp_installations(self) -> PostgresMcpInstallationRepository:
        """Repository for MCP catalog installations."""
        return self._require_connected(
            self._mcp_installations,
            "mcp_installations",
        )

    @property
    def org_facts(self) -> PostgresOrgFactRepository:
        """Repository for organizational fact persistence (MVCC)."""
        return self._require_connected(self._org_facts, "org_facts")

    @property
    def ontology_entities(self) -> PostgresOntologyEntityRepository:
        """Repository for ontology entity definitions."""
        return self._require_connected(
            self._ontology_entities,
            "ontology_entities",
        )

    @property
    def ontology_drift(self) -> PostgresOntologyDriftReportRepository:
        """Repository for ontology drift reports."""
        return self._require_connected(
            self._ontology_drift,
            "ontology_drift",
        )

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Construct a lockout repository using this backend's pool."""
        pool = self.get_db()
        return PostgresLockoutRepository(pool, auth_config)

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository on the shared pool.

        ``notify_channel`` enables cross-instance pg_notify publishing
        when the escalation subsystem has enabled it.
        """
        from synthorg.persistence.postgres.escalation_repo import (  # noqa: PLC0415
            PostgresEscalationRepository,
        )

        pool = self.get_db()
        return PostgresEscalationRepository(pool, notify_channel=notify_channel)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend."""
        from synthorg.ontology.versioning import (  # noqa: PLC0415
            create_postgres_ontology_versioning,
        )

        return create_postgres_ontology_versioning(self.get_db())

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.
        """
        result = await self.settings.get(NotBlankStr("_system"), key)
        return result[0] if result is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.
        """
        updated_at = datetime.now(UTC)
        await self.settings.set(
            NotBlankStr("_system"),
            key,
            value,
            updated_at.isoformat(),
        )


# Public re-export for convenience.
__all__ = ["PostgresPersistenceBackend", "dict_row"]
