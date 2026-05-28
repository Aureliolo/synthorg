"""Postgres persistence backend implementation.

Implements the ``PersistenceBackend`` protocol on top of psycopg 3 and
``psycopg_pool.AsyncConnectionPool``.  Repositories are instantiated
per-backend on ``connect()`` and receive the shared pool; each pool
checkout is an independent transaction, so this backend's
``write_context`` is a no-op rather than the in-process lock SQLite
acquires to serialize writes across its single connection.

The schema uses native Postgres types (JSONB, TIMESTAMPTZ, BIGINT,
BOOLEAN) -- see ``src/synthorg/persistence/postgres/schema.sql``.  At
the Python level, the protocol surface is identical to the SQLite
backend: callers get Pydantic models back either way.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from psycopg.rows import dict_row
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_NOT_CONNECTED,
)
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
    FineTuneRunRepository,
)
from synthorg.persistence.postgres.agent_state_repo import (
    PostgresAgentStateRepository,
)
from synthorg.persistence.postgres.artifact_repo import PostgresArtifactRepository
from synthorg.persistence.postgres.audit_repository import PostgresAuditRepository
from synthorg.persistence.postgres.backend_connection import PostgresConnectionMixin
from synthorg.persistence.postgres.backend_migration import PostgresMigrationMixin
from synthorg.persistence.postgres.ceremony_scheduler_state_repo import (
    PostgresCeremonySchedulerStateRepository,
)
from synthorg.persistence.postgres.checkpoint_repo import (
    PostgresCheckpointRepository,
)
from synthorg.persistence.postgres.circuit_breaker_repo import (
    PostgresCircuitBreakerStateRepository,
)
from synthorg.persistence.postgres.codebase_structure_map_repo import (
    PostgresCodebaseStructureMapRepository,
)
from synthorg.persistence.postgres.connection_repo import (
    PostgresConnectionRepository,
)
from synthorg.persistence.postgres.connection_secret_repo import (
    PostgresConnectionSecretRepository,
)
from synthorg.persistence.postgres.custom_rule_repo import (
    PostgresCustomRuleRepository,
)
from synthorg.persistence.postgres.decision_repo import PostgresDecisionRepository
from synthorg.persistence.postgres.docs_repo import PostgresDocsRepository
from synthorg.persistence.postgres.fine_tune_repo import (
    PostgresFineTuneCheckpointRepository,
    PostgresFineTuneRunRepository,
)
from synthorg.persistence.postgres.flight_recorder_repo import (
    PostgresFlightRecorderFrameRepository,
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
from synthorg.persistence.postgres.knowledge_provenance_repo import (
    PostgresChunkProvenanceRepository,
)
from synthorg.persistence.postgres.knowledge_source_repo import (
    PostgresKnowledgeSourceRepository,
)
from synthorg.persistence.postgres.lockout_repo import (
    PostgresLockoutRepository,
)
from synthorg.persistence.postgres.mcp_installation_repo import (
    PostgresMcpInstallationRepository,
)
from synthorg.persistence.postgres.meeting_cooldown_repo import (
    PostgresMeetingCooldownRepository,
)
from synthorg.persistence.postgres.oauth_state_repo import (
    PostgresOAuthStateRepository,
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
from synthorg.persistence.postgres.preset_override_repo import (
    PostgresPresetOverrideRepo,
)
from synthorg.persistence.postgres.preset_repo import (
    PostgresPersonalityPresetRepository,
)
from synthorg.persistence.postgres.principle_override_repo import (
    PostgresPrincipleOverrideRepository,
)
from synthorg.persistence.postgres.project_cost_aggregate_repo import (
    PostgresProjectCostAggregateRepository,
)
from synthorg.persistence.postgres.project_environment_repo import (
    PostgresProjectEnvironmentRepository,
)
from synthorg.persistence.postgres.project_repo import PostgresProjectRepository
from synthorg.persistence.postgres.project_workspace_repo import (
    PostgresProjectWorkspaceRepository,
)
from synthorg.persistence.postgres.provider_audit_repo import (
    PostgresProviderAuditRepo,
)
from synthorg.persistence.postgres.refresh_repo import (
    PostgresRefreshTokenRepository,
)
from synthorg.persistence.postgres.repositories import (
    PostgresCostRecordRepository,
    PostgresMessageRepository,
    PostgresTaskRepository,
)
from synthorg.persistence.postgres.research_run_repo import (
    PostgresResearchRunRepository,
)
from synthorg.persistence.postgres.risk_override_repo import (
    PostgresRiskOverrideRepository,
)
from synthorg.persistence.postgres.seen_claims_repo import (
    PostgresSeenClaimsRepository,
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
from synthorg.persistence.postgres.tracked_container_repo import (
    PostgresTrackedContainerRepository,
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
from synthorg.persistence.postgres.webhook_receipt_repo import (
    PostgresWebhookReceiptRepository,
)
from synthorg.persistence.postgres.workflow_definition_repo import (
    PostgresWorkflowDefinitionRepository,
)
from synthorg.persistence.postgres.workflow_execution_repo import (
    PostgresWorkflowExecutionRepository,
)
from synthorg.persistence.settings_protocol import SettingRow

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.auth.config import AuthConfig
    from synthorg.hr.persistence_protocol import (
        CollaborationMetricRepository,
        LifecycleEventRepository,
        TaskMetricRepository,
    )
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.agent_state_protocol import AgentStateRepository
    from synthorg.persistence.artifact_protocol import ArtifactRepository
    from synthorg.persistence.audit_protocol import AuditRepository
    from synthorg.persistence.auth_protocol import (
        LockoutRepository,
        RefreshTokenRepository,
        SessionRepository,
    )
    from synthorg.persistence.ceremony_scheduler_state_protocol import (
        CeremonySchedulerStateRepository,
    )
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )
    from synthorg.persistence.circuit_breaker_protocol import (
        CircuitBreakerStateRepository,
    )
    from synthorg.persistence.codebase_structure_map_protocol import (
        CodebaseStructureMapRepository,
    )
    from synthorg.persistence.connection_protocol import (
        ConnectionRepository,
        ConnectionSecretRepository,
        OAuthStateRepository,
        WebhookReceiptRepository,
    )
    from synthorg.persistence.cost_record_protocol import CostRecordRepository
    from synthorg.persistence.custom_rule_protocol import CustomRuleRepository
    from synthorg.persistence.decision_protocol import DecisionRepository
    from synthorg.persistence.docs_protocol import DocsRepository
    from synthorg.persistence.escalation_protocol import EscalationQueueRepository
    from synthorg.persistence.flight_recorder_protocol import (
        FlightRecorderFrameRepository,
    )
    from synthorg.persistence.idempotency_protocol import IdempotencyRepository
    from synthorg.persistence.knowledge_protocol import (
        ChunkProvenanceRepository,
        KnowledgeSourceRepository,
    )
    from synthorg.persistence.mcp_protocol import McpInstallationRepository
    from synthorg.persistence.meeting_cooldown_protocol import (
        MeetingCooldownRepository,
    )
    from synthorg.persistence.memory_protocol import OrgFactRepository
    from synthorg.persistence.message_protocol import MessageRepository
    from synthorg.persistence.ontology_protocol import (
        OntologyDriftReportRepository,
        OntologyEntityRepository,
    )
    from synthorg.persistence.parked_context_protocol import (
        ParkedContextRepository,
    )
    from synthorg.persistence.preset_override_protocol import PresetOverrideRepo
    from synthorg.persistence.preset_protocol import PersonalityPresetRepository
    from synthorg.persistence.principle_override_protocol import (
        PrincipleOverrideRepository,
    )
    from synthorg.persistence.project_cost_aggregate_protocol import (
        ProjectCostAggregateRepository,
    )
    from synthorg.persistence.project_environment_protocol import (
        ProjectEnvironmentRepository,
    )
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.persistence.project_workspace_protocol import (
        ProjectWorkspaceRepository,
    )
    from synthorg.persistence.provider_audit_protocol import ProviderAuditRepo
    from synthorg.persistence.research_protocol import ResearchRunRepository
    from synthorg.persistence.risk_override_protocol import RiskOverrideRepository
    from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
    from synthorg.persistence.settings_protocol import SettingsRepository
    from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository
    from synthorg.persistence.subworkflow_protocol import SubworkflowRepository
    from synthorg.persistence.task_protocol import TaskRepository
    from synthorg.persistence.tracked_container_protocol import (
        TrackedContainerRepository,
    )
    from synthorg.persistence.training_protocol import (
        TrainingPlanRepository,
        TrainingResultRepository,
    )
    from synthorg.persistence.user_protocol import (
        ApiKeyRepository,
        UserRepository,
    )
    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionRepository,
    )
    from synthorg.persistence.workflow_execution_protocol import (
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
        # Repository attributes -- instantiated lazily on connect.
        self._artifacts: ArtifactRepository | None = None
        self._projects: ProjectRepository | None = None
        self._project_workspaces: ProjectWorkspaceRepository | None = None
        self._codebase_structure_maps: CodebaseStructureMapRepository | None = None
        self._project_environments: ProjectEnvironmentRepository | None = None
        self._project_docs: DocsRepository | None = None
        self._knowledge_sources: KnowledgeSourceRepository | None = None
        self._knowledge_provenance: ChunkProvenanceRepository | None = None
        self._research_runs: ResearchRunRepository | None = None
        self._tasks: TaskRepository | None = None
        self._cost_records: CostRecordRepository | None = None
        self._messages: MessageRepository | None = None
        self._lifecycle_events: LifecycleEventRepository | None = None
        self._task_metrics: TaskMetricRepository | None = None
        self._collaboration_metrics: CollaborationMetricRepository | None = None
        self._parked_contexts: ParkedContextRepository | None = None
        self._audit_entries: AuditRepository | None = None
        self._provider_audit_events: PostgresProviderAuditRepo | None = None
        self._preset_overrides: PostgresPresetOverrideRepo | None = None
        self._users: UserRepository | None = None
        self._api_keys: ApiKeyRepository | None = None
        self._checkpoints: CheckpointRepository | None = None
        self._flight_recorder_frames: FlightRecorderFrameRepository | None = None
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
        self._ceremony_scheduler_state: CeremonySchedulerStateRepository | None = None
        self._meeting_cooldown: MeetingCooldownRepository | None = None
        self._tracked_containers: TrackedContainerRepository | None = None
        self._training_plans: PostgresTrainingPlanRepository | None = None
        self._training_results: PostgresTrainingResultRepository | None = None
        self._sessions: PostgresSessionRepository | None = None
        self._refresh_tokens: PostgresRefreshTokenRepository | None = None
        self._idempotency_keys: PostgresIdempotencyRepository | None = None
        self._seen_claims: PostgresSeenClaimsRepository | None = None
        self._principle_overrides: PostgresPrincipleOverrideRepository | None = None
        self._mcp_installations: PostgresMcpInstallationRepository | None = None
        self._custom_rules: PostgresCustomRuleRepository | None = None
        self._org_facts: PostgresOrgFactRepository | None = None
        self._ontology_entities: PostgresOntologyEntityRepository | None = None
        self._ontology_drift: PostgresOntologyDriftReportRepository | None = None
        self._connections: PostgresConnectionRepository | None = None
        self._connection_secrets: PostgresConnectionSecretRepository | None = None
        self._oauth_states: PostgresOAuthStateRepository | None = None
        self._webhook_receipts: PostgresWebhookReceiptRepository | None = None
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
        self._project_workspaces = None
        self._codebase_structure_maps = None
        self._knowledge_sources = None
        self._knowledge_provenance = None
        self._research_runs = None
        self._project_environments = None
        self._project_docs = None
        self._tasks = None
        self._cost_records = None
        self._messages = None
        self._lifecycle_events = None
        self._task_metrics = None
        self._collaboration_metrics = None
        self._parked_contexts = None
        self._audit_entries = None
        self._provider_audit_events = None
        self._preset_overrides = None
        self._users = None
        self._api_keys = None
        self._checkpoints = None
        self._flight_recorder_frames = None
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
        self._ceremony_scheduler_state = None
        self._meeting_cooldown = None
        self._tracked_containers = None
        self._project_cost_aggregates = None
        self._training_plans = None
        self._training_results = None
        self._sessions = None
        self._refresh_tokens = None
        self._idempotency_keys = None
        self._seen_claims = None
        self._principle_overrides = None
        self._mcp_installations = None
        self._custom_rules = None
        self._org_facts = None
        self._ontology_entities = None
        self._ontology_drift = None
        self._fine_tune_runs = None
        self._fine_tune_checkpoints = None
        self._connections = None
        self._connection_secrets = None
        self._oauth_states = None
        self._webhook_receipts = None

    def _create_repositories(self) -> None:
        """Instantiate all repository objects from the active pool."""
        assert self._pool is not None  # noqa: S101
        pool = self._pool

        # Core domain repositories.
        self._artifacts = PostgresArtifactRepository(pool)
        self._projects = PostgresProjectRepository(pool)
        self._project_workspaces = PostgresProjectWorkspaceRepository(pool)
        self._codebase_structure_maps = PostgresCodebaseStructureMapRepository(pool)
        self._knowledge_sources = PostgresKnowledgeSourceRepository(pool)
        self._knowledge_provenance = PostgresChunkProvenanceRepository(pool)
        self._research_runs = PostgresResearchRunRepository(pool)
        self._project_environments = PostgresProjectEnvironmentRepository(pool)
        self._project_docs = PostgresDocsRepository(pool)
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
        self._provider_audit_events = PostgresProviderAuditRepo(pool)
        self._preset_overrides = PostgresPresetOverrideRepo(pool)
        self._users = PostgresUserRepository(pool)
        self._api_keys = PostgresApiKeyRepository(pool)
        self._checkpoints = PostgresCheckpointRepository(pool)
        self._flight_recorder_frames = PostgresFlightRecorderFrameRepository(pool)
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
        # transactions handle isolation at the database level, so this
        # backend's ``write_context`` is a no-op and repositories do not
        # take a ``write_context`` constructor argument.
        self._decision_records = PostgresDecisionRepository(pool)
        self._risk_overrides = PostgresRiskOverrideRepository(pool)
        self._ssrf_violations = PostgresSsrfViolationRepository(pool)
        self._circuit_breaker_state = PostgresCircuitBreakerStateRepository(pool)
        self._ceremony_scheduler_state = PostgresCeremonySchedulerStateRepository(pool)
        self._meeting_cooldown = PostgresMeetingCooldownRepository(pool)
        self._tracked_containers = PostgresTrackedContainerRepository(pool)
        self._project_cost_aggregates = PostgresProjectCostAggregateRepository(pool)
        self._training_plans = PostgresTrainingPlanRepository(pool)
        self._training_results = PostgresTrainingResultRepository(pool)
        self._sessions = PostgresSessionRepository(pool)
        self._refresh_tokens = PostgresRefreshTokenRepository(pool)
        self._idempotency_keys = PostgresIdempotencyRepository(pool)
        self._seen_claims = PostgresSeenClaimsRepository(pool)
        self._principle_overrides = PostgresPrincipleOverrideRepository(pool)
        self._mcp_installations = PostgresMcpInstallationRepository(pool)
        self._custom_rules = PostgresCustomRuleRepository(pool)
        self._org_facts = PostgresOrgFactRepository(pool)
        self._ontology_entities = PostgresOntologyEntityRepository(pool)
        self._ontology_drift = PostgresOntologyDriftReportRepository(pool)
        self._fine_tune_runs = PostgresFineTuneRunRepository(pool)
        self._fine_tune_checkpoints = PostgresFineTuneCheckpointRepository(pool)
        self._connections = PostgresConnectionRepository(pool)
        self._connection_secrets = PostgresConnectionSecretRepository(pool)
        self._oauth_states = PostgresOAuthStateRepository(pool)
        self._webhook_receipts = PostgresWebhookReceiptRepository(pool)

    def get_db(self) -> AsyncConnectionPool:
        """Return the shared connection pool.

        Raises:
            PersistenceConnectionError: If not yet connected.

        Returns:
            The active connection pool (raises if not connected).
        """
        if self._pool is None:
            msg = "Postgres backend not connected"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return self._pool

    @asynccontextmanager
    async def write_context(self) -> AsyncIterator[None]:
        """No-op for Postgres.

        Each repository checks out its own connection from the async
        pool; transactions on different connections cannot interleave
        at the statement level. Implementing the protocol method as a
        no-op keeps the cross-backend interface honest and lets
        callers write ``async with backend.write_context()`` without
        backend-specific branching.
        """
        yield

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an open pool.

        Returns:
            ``True`` when the backend has an active connection, ``False`` otherwise.
        """
        return self._pool is not None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("postgres")

    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        """Return the backend discriminator (``"postgres"``).

        Returns:
            Result of type ``Literal['sqlite', 'postgres']``.
        """
        return "postgres"

    @property
    def config(self) -> PostgresConfig:
        """Public read-only view of the backend's Postgres config.

        Exposed so callers needing the connection details (the
        backup-handler factory) do not have to reach for the
        private ``_config`` attribute.

        Returns:
            Result of type ``PostgresConfig``.
        """
        return self._config

    def _require_connected[T](self, repo: T | None, name: str) -> T:
        """Return *repo* or raise if the backend is not connected.

        Args:
            repo: Repository instance (``None`` when disconnected or
                not yet ported).
            name: Repository name for the error message.

        Raises:
            PersistenceConnectionError: If *repo* is ``None``.

        Returns:
            Result of type ``T``.
        """
        if repo is None:
            if self._pool is None:
                msg = f"Not connected -- call connect() before accessing {name}"
            else:
                msg = f"Postgres {name} repository is not yet implemented"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return repo

    @property
    def tasks(self) -> TaskRepository:
        """Repository for Task persistence.

        Returns:
            Result of type ``TaskRepository``.
        """
        return self._require_connected(self._tasks, "tasks")

    @property
    def cost_records(self) -> CostRecordRepository:
        """Repository for CostRecord persistence.

        Returns:
            Result of type ``CostRecordRepository``.
        """
        return self._require_connected(self._cost_records, "cost_records")

    @property
    def messages(self) -> MessageRepository:
        """Repository for Message persistence.

        Returns:
            Result of type ``MessageRepository``.
        """
        return self._require_connected(self._messages, "messages")

    @property
    def lifecycle_events(self) -> LifecycleEventRepository:
        """Repository for AgentLifecycleEvent persistence.

        Returns:
            Result of type ``LifecycleEventRepository``.
        """
        return self._require_connected(self._lifecycle_events, "lifecycle_events")

    @property
    def task_metrics(self) -> TaskMetricRepository:
        """Repository for TaskMetricRecord persistence.

        Returns:
            Result of type ``TaskMetricRepository``.
        """
        return self._require_connected(self._task_metrics, "task_metrics")

    @property
    def collaboration_metrics(self) -> CollaborationMetricRepository:
        """Repository for CollaborationMetricRecord persistence.

        Returns:
            Result of type ``CollaborationMetricRepository``.
        """
        return self._require_connected(
            self._collaboration_metrics, "collaboration_metrics"
        )

    @property
    def parked_contexts(self) -> ParkedContextRepository:
        """Repository for ParkedContext persistence.

        Returns:
            Result of type ``ParkedContextRepository``.
        """
        return self._require_connected(self._parked_contexts, "parked_contexts")

    @property
    def audit_entries(self) -> AuditRepository:
        """Repository for AuditEntry persistence.

        Returns:
            Result of type ``AuditRepository``.
        """
        return self._require_connected(self._audit_entries, "audit_entries")

    @property
    def provider_audit_events(self) -> ProviderAuditRepo:
        """Repository for the provider mutation audit log.

        Returns:
            Result of type ``ProviderAuditRepo``.
        """
        return self._require_connected(
            self._provider_audit_events,
            "provider_audit_events",
        )

    @property
    def preset_overrides(self) -> PresetOverrideRepo:
        """Repository for operator-authored provider preset overrides.

        Returns:
            Result of type ``PresetOverrideRepo``.
        """
        return self._require_connected(
            self._preset_overrides,
            "preset_overrides",
        )

    @property
    def decision_records(self) -> DecisionRepository:
        """Repository for DecisionRecord persistence.

        Returns:
            Result of type ``DecisionRepository``.
        """
        return self._require_connected(self._decision_records, "decision_records")

    @property
    def users(self) -> UserRepository:
        """Repository for User persistence.

        Returns:
            Result of type ``UserRepository``.
        """
        return self._require_connected(self._users, "users")

    @property
    def api_keys(self) -> ApiKeyRepository:
        """Repository for ApiKey persistence.

        Returns:
            Result of type ``ApiKeyRepository``.
        """
        return self._require_connected(self._api_keys, "api_keys")

    @property
    def checkpoints(self) -> CheckpointRepository:
        """Repository for Checkpoint persistence.

        Returns:
            Result of type ``CheckpointRepository``.
        """
        return self._require_connected(self._checkpoints, "checkpoints")

    @property
    def flight_recorder_frames(self) -> FlightRecorderFrameRepository:
        """Repository for flight-recorder frame persistence.

        Returns:
            Result of type ``FlightRecorderFrameRepository``.
        """
        return self._require_connected(
            self._flight_recorder_frames,
            "flight_recorder_frames",
        )

    @property
    def heartbeats(self) -> HeartbeatRepository:
        """Repository for Heartbeat persistence.

        Returns:
            Result of type ``HeartbeatRepository``.
        """
        return self._require_connected(self._heartbeats, "heartbeats")

    @property
    def agent_states(self) -> AgentStateRepository:
        """Repository for AgentRuntimeState persistence.

        Returns:
            Result of type ``AgentStateRepository``.
        """
        return self._require_connected(self._agent_states, "agent_states")

    @property
    def settings(self) -> SettingsRepository:
        """Repository for namespaced settings persistence.

        Returns:
            Result of type ``SettingsRepository``.
        """
        return self._require_connected(self._settings, "settings")

    @property
    def artifacts(self) -> ArtifactRepository:
        """Repository for Artifact persistence.

        Returns:
            Result of type ``ArtifactRepository``.
        """
        return self._require_connected(self._artifacts, "artifacts")

    @property
    def projects(self) -> ProjectRepository:
        """Repository for Project persistence.

        Returns:
            Result of type ``ProjectRepository``.
        """
        return self._require_connected(self._projects, "projects")

    @property
    def project_workspaces(self) -> ProjectWorkspaceRepository:
        """Repository for persistent per-project workspace mappings.

        Returns:
            Result of type ``ProjectWorkspaceRepository``.
        """
        return self._require_connected(self._project_workspaces, "project_workspaces")

    @property
    def codebase_structure_maps(self) -> CodebaseStructureMapRepository:
        """Repository for per-project brownfield codebase structure maps.

        Returns:
            Result of type ``CodebaseStructureMapRepository``.
        """
        return self._require_connected(
            self._codebase_structure_maps, "codebase_structure_maps"
        )

    @property
    def project_environments(self) -> ProjectEnvironmentRepository:
        """Repository for persistent per-project environment mappings.

        Returns:
            Result of type ``ProjectEnvironmentRepository``.
        """
        return self._require_connected(
            self._project_environments, "project_environments"
        )

    @property
    def project_docs(self) -> DocsRepository:
        """Repository for living-documentation metadata persistence.

        Returns:
            Result of type ``DocsRepository``.
        """
        return self._require_connected(self._project_docs, "project_docs")

    @property
    def knowledge_sources(self) -> KnowledgeSourceRepository:
        """Repository for the knowledge-source registry.

        Returns:
            Result of type ``KnowledgeSourceRepository``.
        """
        return self._require_connected(self._knowledge_sources, "knowledge_sources")

    @property
    def research_runs(self) -> ResearchRunRepository:
        """Repository for the research-run record.

        Returns:
            Result of type ``ResearchRunRepository``.
        """
        return self._require_connected(self._research_runs, "research_runs")

    @property
    def knowledge_provenance(self) -> ChunkProvenanceRepository:
        """Repository for per-chunk knowledge provenance.

        Returns:
            Result of type ``ChunkProvenanceRepository``.
        """
        return self._require_connected(
            self._knowledge_provenance, "knowledge_provenance"
        )

    @property
    def custom_presets(self) -> PersonalityPresetRepository:
        """Repository for custom personality preset persistence.

        Returns:
            Result of type ``PersonalityPresetRepository``.
        """
        return self._require_connected(self._custom_presets, "custom_presets")

    @property
    def workflow_definitions(self) -> WorkflowDefinitionRepository:
        """Repository for workflow definition persistence.

        Returns:
            Result of type ``WorkflowDefinitionRepository``.
        """
        return self._require_connected(
            self._workflow_definitions, "workflow_definitions"
        )

    @property
    def workflow_executions(self) -> WorkflowExecutionRepository:
        """Repository for workflow execution persistence.

        Returns:
            Result of type ``WorkflowExecutionRepository``.
        """
        return self._require_connected(self._workflow_executions, "workflow_executions")

    @property
    def subworkflows(self) -> SubworkflowRepository:
        """Repository for subworkflow registry persistence.

        Returns:
            Result of type ``SubworkflowRepository``.
        """
        return self._require_connected(self._subworkflows, "subworkflows")

    @property
    def workflow_versions(self) -> VersionRepository[WorkflowDefinition]:
        """Repository for workflow definition version persistence.

        Returns:
            Result of type ``VersionRepository[WorkflowDefinition]``.
        """
        return self._require_connected(self._workflow_versions, "workflow_versions")

    @property
    def identity_versions(self) -> VersionRepository[AgentIdentity]:
        """Repository for AgentIdentity version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[AgentIdentity]``.
        """
        return self._require_connected(self._identity_versions, "identity_versions")

    @property
    def evaluation_config_versions(
        self,
    ) -> VersionRepository[EvaluationConfig]:
        """Repository for EvaluationConfig version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[EvaluationConfig]``.
        """
        return self._require_connected(
            self._evaluation_config_versions, "evaluation_config_versions"
        )

    @property
    def budget_config_versions(self) -> VersionRepository[BudgetConfig]:
        """Repository for BudgetConfig version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[BudgetConfig]``.
        """
        return self._require_connected(
            self._budget_config_versions, "budget_config_versions"
        )

    @property
    def company_versions(self) -> VersionRepository[Company]:
        """Repository for Company version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[Company]``.
        """
        return self._require_connected(self._company_versions, "company_versions")

    @property
    def role_versions(self) -> VersionRepository[Role]:
        """Repository for Role version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[Role]``.
        """
        return self._require_connected(self._role_versions, "role_versions")

    @property
    def risk_overrides(self) -> RiskOverrideRepository:
        """Repository for risk tier override persistence.

        Returns:
            Result of type ``RiskOverrideRepository``.
        """
        return self._require_connected(self._risk_overrides, "risk_overrides")

    @property
    def ssrf_violations(self) -> SsrfViolationRepository:
        """Repository for SSRF violation record persistence.

        Returns:
            Result of type ``SsrfViolationRepository``.
        """
        return self._require_connected(self._ssrf_violations, "ssrf_violations")

    @property
    def circuit_breaker_state(self) -> CircuitBreakerStateRepository:
        """Repository for circuit breaker state persistence.

        Returns:
            Result of type ``CircuitBreakerStateRepository``.
        """
        return self._require_connected(
            self._circuit_breaker_state, "circuit_breaker_state"
        )

    @property
    def ceremony_scheduler_state(self) -> CeremonySchedulerStateRepository:
        """Repository for ceremony scheduler per-sprint state snapshots.

        Returns:
            Result of type ``CeremonySchedulerStateRepository``.
        """
        return self._require_connected(
            self._ceremony_scheduler_state, "ceremony_scheduler_state"
        )

    @property
    def meeting_cooldown(self) -> MeetingCooldownRepository:
        """Repository for meeting cooldown last-triggered timestamps.

        Returns:
            Result of type ``MeetingCooldownRepository``.
        """
        return self._require_connected(self._meeting_cooldown, "meeting_cooldown")

    @property
    def tracked_containers(self) -> TrackedContainerRepository:
        """Repository for Docker sandbox tracked-container records.

        Returns:
            Result of type ``TrackedContainerRepository``.
        """
        return self._require_connected(self._tracked_containers, "tracked_containers")

    @property
    def project_cost_aggregates(self) -> ProjectCostAggregateRepository:
        """Repository for durable project cost aggregates.

        Raises:
            PersistenceConnectionError: If not connected.

        Returns:
            Result of type ``ProjectCostAggregateRepository``.
        """
        return self._require_connected(
            self._project_cost_aggregates,
            "project_cost_aggregates",
        )

    @property
    def fine_tune_checkpoints(self) -> FineTuneCheckpointRepository:
        """Repository for fine-tune checkpoint persistence.

        Returns:
            Result of type ``FineTuneCheckpointRepository``.
        """
        return self._require_connected(
            self._fine_tune_checkpoints,
            "fine_tune_checkpoints",
        )

    @property
    def fine_tune_runs(self) -> FineTuneRunRepository:
        """Repository for fine-tune pipeline runs.

        Returns:
            Result of type ``FineTuneRunRepository``.
        """
        return self._require_connected(self._fine_tune_runs, "fine_tune_runs")

    @property
    def connections(self) -> ConnectionRepository:
        """Repository for external service connection persistence.

        Returns:
            Result of type ``ConnectionRepository``.
        """
        return self._require_connected(self._connections, "connections")

    @property
    def connection_secrets(self) -> ConnectionSecretRepository:
        """Repository for encrypted connection secret persistence.

        Returns:
            Result of type ``ConnectionSecretRepository``.
        """
        return self._require_connected(
            self._connection_secrets,
            "connection_secrets",
        )

    @property
    def oauth_states(self) -> OAuthStateRepository:
        """Repository for transient OAuth state persistence.

        Returns:
            Result of type ``OAuthStateRepository``.
        """
        return self._require_connected(self._oauth_states, "oauth_states")

    @property
    def webhook_receipts(self) -> WebhookReceiptRepository:
        """Repository for webhook receipt log persistence.

        Returns:
            Result of type ``WebhookReceiptRepository``.
        """
        return self._require_connected(
            self._webhook_receipts,
            "webhook_receipts",
        )

    @property
    def training_plans(self) -> TrainingPlanRepository:
        """Repository for training plan persistence.

        Returns:
            Result of type ``TrainingPlanRepository``.
        """
        return self._require_connected(
            self._training_plans,
            "training_plans",
        )

    @property
    def training_results(self) -> TrainingResultRepository:
        """Repository for training result persistence.

        Returns:
            Result of type ``TrainingResultRepository``.
        """
        return self._require_connected(
            self._training_results,
            "training_results",
        )

    @property
    def custom_rules(self) -> CustomRuleRepository:
        """Repository for custom signal rule persistence.

        Returns:
            Result of type ``CustomRuleRepository``.
        """
        return self._require_connected(self._custom_rules, "custom_rules")

    @property
    def sessions(self) -> SessionRepository:
        """Repository for hybrid session state (durable + in-memory cache).

        Returns:
            Result of type ``SessionRepository``.
        """
        return self._require_connected(self._sessions, "sessions")

    @property
    def refresh_tokens(self) -> RefreshTokenRepository:
        """Repository for single-use refresh-token rotation.

        Returns:
            Result of type ``RefreshTokenRepository``.
        """
        return self._require_connected(
            self._refresh_tokens,
            "refresh_tokens",
        )

    @property
    def idempotency_keys(self) -> IdempotencyRepository:
        """Repository for persistent idempotency keys.

        Returns:
            Result of type ``IdempotencyRepository``.
        """
        return self._require_connected(
            self._idempotency_keys,
            "idempotency_keys",
        )

    @property
    def seen_claims(self) -> SeenClaimsRepository:
        """Repository for worker TaskClaim dedup persistence.

        Returns:
            Result of type ``SeenClaimsRepository``.
        """
        return self._require_connected(
            self._seen_claims,
            "seen_claims",
        )

    @property
    def principle_overrides(self) -> PrincipleOverrideRepository:
        """Repository for rollback-restored principle overrides.

        Returns:
            Result of type ``PrincipleOverrideRepository``.
        """
        return self._require_connected(
            self._principle_overrides,
            "principle_overrides",
        )

    @property
    def mcp_installations(self) -> McpInstallationRepository:
        """Repository for MCP catalog installations.

        Returns:
            Result of type ``McpInstallationRepository``.
        """
        return self._require_connected(
            self._mcp_installations,
            "mcp_installations",
        )

    @property
    def org_facts(self) -> OrgFactRepository:
        """Repository for organizational fact persistence (MVCC).

        Returns:
            Result of type ``OrgFactRepository``.
        """
        return self._require_connected(self._org_facts, "org_facts")

    @property
    def ontology_entities(self) -> OntologyEntityRepository:
        """Repository for ontology entity definitions.

        Returns:
            Result of type ``OntologyEntityRepository``.
        """
        return self._require_connected(
            self._ontology_entities,
            "ontology_entities",
        )

    @property
    def ontology_drift(self) -> OntologyDriftReportRepository:
        """Repository for ontology drift reports.

        Returns:
            Result of type ``OntologyDriftReportRepository``.
        """
        return self._require_connected(
            self._ontology_drift,
            "ontology_drift",
        )

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Construct a lockout repository using this backend's pool.

        Returns:
            Result of type ``LockoutRepository``.
        """
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

        Returns:
            Result of type ``EscalationQueueRepository``.
        """
        from synthorg.persistence.postgres.escalation_repo import (  # noqa: PLC0415
            PostgresEscalationRepository,
        )

        pool = self.get_db()
        return PostgresEscalationRepository(pool, notify_channel=notify_channel)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend.

        Returns:
            Result of type ``VersioningService[EntityDefinition]``.
        """
        from synthorg.persistence.postgres.ontology_versioning import (  # noqa: PLC0415
            create_postgres_ontology_versioning,
        )

        return create_postgres_ontology_versioning(self.get_db())

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.

        Returns:
            The setting value as ``str``, or ``None`` when no row matches.
        """
        entity = await self.settings.get((NotBlankStr("_system"), key))
        return entity.value if entity is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.
        """
        updated_at = datetime.now(UTC)
        entity = SettingRow(
            namespace=NotBlankStr("_system"),
            key=key,
            value=value,
            updated_at=format_iso_utc(updated_at),
        )
        await self.settings.save(entity)


# Public re-export for convenience.
__all__ = ["PostgresPersistenceBackend", "dict_row"]
