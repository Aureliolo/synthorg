# module-kind: complex_service
"""Repository property accessors for the SQLite backend.

This mixin holds the read-only ``@property`` accessors that
``SQLitePersistenceBackend`` exposes for every domain repository. The
accessors all share a single pattern (``_require_connected`` against a
private ``_<name>`` attribute populated by ``_create_repositories``),
so collecting them here keeps ``backend.py`` focused on lifecycle and
wiring rather than ~330 lines of mechanical property forwarding.

One cohesive responsibility: provide the connection-checked accessor
for every persistence repository on the SQLite backend. The size
scales linearly with the repository count, and the ``_require_connected``
contract is uniform across them all; per-domain sibling mixins would
fragment the connection-state invariant across files without
reducing total LOC.
"""

from typing import TYPE_CHECKING

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_NOT_CONNECTED,
)

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.engine.workflow.definition import WorkflowDefinition
    from synthorg.hr.evaluation.config import EvaluationConfig
    from synthorg.hr.persistence_protocol import (
        CollaborationMetricRepository,
        LifecycleEventRepository,
        TaskMetricRepository,
    )
    from synthorg.persistence.agent_state_protocol import AgentStateRepository
    from synthorg.persistence.artifact_protocol import ArtifactRepository
    from synthorg.persistence.audit_protocol import AuditRepository
    from synthorg.persistence.auth_protocol import (
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
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
        FineTuneRunRepository,
    )
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
    from synthorg.persistence.ssrf_violation_protocol import (
        SsrfViolationRepository,
    )
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

logger = get_logger(__name__)


class _BackendRepositoryAccessors:
    """Mixin holding ``@property`` accessors for every repository.

    The hosting class (``SQLitePersistenceBackend``) is responsible for
    setting each ``_<name>`` attribute via ``__init__`` (``None`` until
    ``connect()``) and populating it from ``_create_repositories``.
    """

    _tasks: TaskRepository | None
    _cost_records: CostRecordRepository | None
    _messages: MessageRepository | None
    _lifecycle_events: LifecycleEventRepository | None
    _task_metrics: TaskMetricRepository | None
    _collaboration_metrics: CollaborationMetricRepository | None
    _parked_contexts: ParkedContextRepository | None
    _audit_entries: AuditRepository | None
    _provider_audit_events: ProviderAuditRepo | None
    _preset_overrides: PresetOverrideRepo | None
    _decision_records: DecisionRepository | None
    _users: UserRepository | None
    _api_keys: ApiKeyRepository | None
    _checkpoints: CheckpointRepository | None
    _flight_recorder_frames: FlightRecorderFrameRepository | None
    _heartbeats: HeartbeatRepository | None
    _agent_states: AgentStateRepository | None
    _settings: SettingsRepository | None
    _artifacts: ArtifactRepository | None
    _projects: ProjectRepository | None
    _project_workspaces: ProjectWorkspaceRepository | None
    _codebase_structure_maps: CodebaseStructureMapRepository | None
    _project_environments: ProjectEnvironmentRepository | None
    _project_docs: DocsRepository | None
    _knowledge_sources: KnowledgeSourceRepository | None
    _knowledge_provenance: ChunkProvenanceRepository | None
    _research_runs: ResearchRunRepository | None
    _project_cost_aggregates: ProjectCostAggregateRepository | None
    _fine_tune_checkpoints: FineTuneCheckpointRepository | None
    _fine_tune_runs: FineTuneRunRepository | None
    _custom_presets: PersonalityPresetRepository | None
    _workflow_definitions: WorkflowDefinitionRepository | None
    _workflow_executions: WorkflowExecutionRepository | None
    _subworkflows: SubworkflowRepository | None
    _workflow_versions: VersionRepository[WorkflowDefinition] | None
    _identity_versions: VersionRepository[AgentIdentity] | None
    _evaluation_config_versions: VersionRepository[EvaluationConfig] | None
    _budget_config_versions: VersionRepository[BudgetConfig] | None
    _company_versions: VersionRepository[Company] | None
    _role_versions: VersionRepository[Role] | None
    _risk_overrides: RiskOverrideRepository | None
    _ssrf_violations: SsrfViolationRepository | None
    _circuit_breaker_state: CircuitBreakerStateRepository | None
    _ceremony_scheduler_state: CeremonySchedulerStateRepository | None
    _meeting_cooldown: MeetingCooldownRepository | None
    _tracked_containers: TrackedContainerRepository | None
    _connections: ConnectionRepository | None
    _connection_secrets: ConnectionSecretRepository | None
    _oauth_states: OAuthStateRepository | None
    _webhook_receipts: WebhookReceiptRepository | None
    _training_plans: TrainingPlanRepository | None
    _training_results: TrainingResultRepository | None
    _custom_rules: CustomRuleRepository | None
    _sessions: SessionRepository | None
    _refresh_tokens: RefreshTokenRepository | None
    _idempotency_keys: IdempotencyRepository | None
    _seen_claims: SeenClaimsRepository | None
    _principle_overrides: PrincipleOverrideRepository | None
    _mcp_installations: McpInstallationRepository | None
    _org_facts: OrgFactRepository | None
    _ontology_entities: OntologyEntityRepository | None
    _ontology_drift: OntologyDriftReportRepository | None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("sqlite")

    def _require_connected[T](self, repo: T | None, name: str) -> T:
        """Return *repo* or raise if the backend is not connected.

        Args:
            repo: Repository instance (``None`` when disconnected).
            name: Repository name for the error message.

        Raises:
            PersistenceConnectionError: If *repo* is ``None``.

        Returns:
            Result of type ``T``.
        """
        if repo is None:
            msg = f"Not connected -- call connect() before accessing {name}"
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
        """Repository for DecisionRecord persistence (decisions drop-box).

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
    def knowledge_provenance(self) -> ChunkProvenanceRepository:
        """Repository for per-chunk knowledge provenance.

        Returns:
            Result of type ``ChunkProvenanceRepository``.
        """
        return self._require_connected(
            self._knowledge_provenance, "knowledge_provenance"
        )

    @property
    def research_runs(self) -> ResearchRunRepository:
        """Repository for the research-run record.

        Returns:
            Result of type ``ResearchRunRepository``.
        """
        return self._require_connected(self._research_runs, "research_runs")

    @property
    def project_cost_aggregates(self) -> ProjectCostAggregateRepository:
        """Repository for durable project cost aggregates.

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
        """Repository for fine-tune pipeline run persistence.

        Returns:
            Result of type ``FineTuneRunRepository``.
        """
        return self._require_connected(
            self._fine_tune_runs,
            "fine_tune_runs",
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
            self._workflow_definitions,
            "workflow_definitions",
        )

    @property
    def workflow_executions(self) -> WorkflowExecutionRepository:
        """Repository for workflow execution persistence.

        Returns:
            Result of type ``WorkflowExecutionRepository``.
        """
        return self._require_connected(
            self._workflow_executions,
            "workflow_executions",
        )

    @property
    def subworkflows(self) -> SubworkflowRepository:
        """Repository for versioned subworkflow persistence.

        Returns:
            Result of type ``SubworkflowRepository``.
        """
        return self._require_connected(
            self._subworkflows,
            "subworkflows",
        )

    @property
    def workflow_versions(self) -> VersionRepository[WorkflowDefinition]:
        """Repository for workflow definition version persistence.

        Returns:
            Result of type ``VersionRepository[WorkflowDefinition]``.
        """
        return self._require_connected(
            self._workflow_versions,
            "workflow_versions",
        )

    @property
    def identity_versions(self) -> VersionRepository[AgentIdentity]:
        """Repository for AgentIdentity version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[AgentIdentity]``.
        """
        return self._require_connected(
            self._identity_versions,
            "identity_versions",
        )

    @property
    def evaluation_config_versions(
        self,
    ) -> VersionRepository[EvaluationConfig]:
        """Repository for EvaluationConfig version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[EvaluationConfig]``.
        """
        return self._require_connected(
            self._evaluation_config_versions,
            "evaluation_config_versions",
        )

    @property
    def budget_config_versions(
        self,
    ) -> VersionRepository[BudgetConfig]:
        """Repository for BudgetConfig version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[BudgetConfig]``.
        """
        return self._require_connected(
            self._budget_config_versions,
            "budget_config_versions",
        )

    @property
    def company_versions(
        self,
    ) -> VersionRepository[Company]:
        """Repository for Company version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[Company]``.
        """
        return self._require_connected(
            self._company_versions,
            "company_versions",
        )

    @property
    def role_versions(
        self,
    ) -> VersionRepository[Role]:
        """Repository for Role version snapshot persistence.

        Returns:
            Result of type ``VersionRepository[Role]``.
        """
        return self._require_connected(
            self._role_versions,
            "role_versions",
        )

    @property
    def risk_overrides(self) -> RiskOverrideRepository:
        """Repository for risk tier override persistence.

        Returns:
            Result of type ``RiskOverrideRepository``.
        """
        return self._require_connected(
            self._risk_overrides,
            "risk_overrides",
        )

    @property
    def ssrf_violations(self) -> SsrfViolationRepository:
        """Repository for SSRF violation record persistence.

        Returns:
            Result of type ``SsrfViolationRepository``.
        """
        return self._require_connected(
            self._ssrf_violations,
            "ssrf_violations",
        )

    @property
    def circuit_breaker_state(self) -> CircuitBreakerStateRepository:
        """Repository for circuit breaker state persistence.

        Returns:
            Result of type ``CircuitBreakerStateRepository``.
        """
        return self._require_connected(
            self._circuit_breaker_state,
            "circuit_breaker_state",
        )

    @property
    def ceremony_scheduler_state(self) -> CeremonySchedulerStateRepository:
        """Repository for ceremony scheduler per-sprint state snapshots.

        Returns:
            Result of type ``CeremonySchedulerStateRepository``.
        """
        return self._require_connected(
            self._ceremony_scheduler_state,
            "ceremony_scheduler_state",
        )

    @property
    def meeting_cooldown(self) -> MeetingCooldownRepository:
        """Repository for meeting cooldown last-triggered timestamps.

        Returns:
            Result of type ``MeetingCooldownRepository``.
        """
        return self._require_connected(
            self._meeting_cooldown,
            "meeting_cooldown",
        )

    @property
    def tracked_containers(self) -> TrackedContainerRepository:
        """Repository for Docker sandbox tracked-container records.

        Returns:
            Result of type ``TrackedContainerRepository``.
        """
        return self._require_connected(
            self._tracked_containers,
            "tracked_containers",
        )

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
        return self._require_connected(
            self._custom_rules,
            "custom_rules",
        )

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
