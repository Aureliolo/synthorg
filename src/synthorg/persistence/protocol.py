# module-kind: declarative
"""PersistenceBackend protocol -- lifecycle + repository access.

Application code depends on this protocol for storage lifecycle
management.  Repository protocols provide entity-level access.
"""

from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from typing import Protocol, runtime_checkable

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.auth.config import AuthConfig
from synthorg.core.company import Company
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
)
from synthorg.hr.persistence_protocol import (
    LifecycleEventRepository,
    TaskMetricRepository,
)

# ``VersioningService[EntityDefinition]`` appears in the
# ``build_ontology_versioning`` return annotation. PEP 649 lazy
# annotation evaluation needs both names in module globals so any
# caller that introspects the ``PersistenceBackend`` protocol via
# ``typing.get_type_hints`` resolves the type without ``NameError``.
from synthorg.ontology.models import (
    EntityDefinition,
)
from synthorg.persistence.agent_contribution_protocol import (
    AgentContributionRepository,
)
from synthorg.persistence.agent_state_protocol import (
    AgentStateRepository,
)
from synthorg.persistence.artifact_protocol import ArtifactRepository
from synthorg.persistence.audit_chain_protocol import AuditChainRepository
from synthorg.persistence.audit_protocol import AuditRepository
from synthorg.persistence.auth_protocol import (
    LockoutRepository,
    RefreshTokenRepository,
    SessionRepository,
)
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRepository,
)
from synthorg.persistence.capability_source_status_protocol import (
    CapabilitySourceStatusRepository,
)
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionRecordRepository,
)
from synthorg.persistence.codebase_structure_map_protocol import (
    CodebaseStructureMapRepository,
)
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportArchiveRepository,
)
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.connection_protocol import (
    ConnectionRepository,
    ConnectionSecretRepository,
    OAuthStateRepository,
    WebhookReceiptRepository,
)
from synthorg.persistence.cost_record_protocol import (
    CostRecordRepository,
)
from synthorg.persistence.custom_rule_protocol import (
    CustomRuleRepository,
)
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.deleted_entity_protocol import (
    DeletedEntityRepository,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptRepository,
)
from synthorg.persistence.docs_protocol import DocsRepository
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportRepository,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
    FineTuneRunRepository,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestRepository,
)
from synthorg.persistence.idempotency_protocol import (
    IdempotencyRepository,
)
from synthorg.persistence.knowledge_protocol import (
    ChunkProvenanceRepository,
    KnowledgeSourceRepository,
)
from synthorg.persistence.knowledge_usage_protocol import (
    KnowledgeUsageRecordRepository,
)
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionRepository,
)
from synthorg.persistence.mcp_protocol import (
    McpInstallationRepository,
)
from synthorg.persistence.memory_protocol import (
    OrgFactRepository,
)
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository
from synthorg.persistence.message_protocol import MessageRepository
from synthorg.persistence.model_capability_score_protocol import (
    ModelCapabilityScoreRepository,
)
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignalRepository,
)
from synthorg.persistence.ontology_protocol import (
    OntologyDriftReportRepository,
    OntologyEntityRepository,
)
from synthorg.persistence.parked_context_protocol import (
    ParkedContextRepository,
)
from synthorg.persistence.plan_comment_protocol import PlanItemCommentRepository
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.preset_override_protocol import (
    PresetOverrideRepo,
)
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.persistence.project_brain_protocol import ProjectBrainRepository
from synthorg.persistence.project_cost_aggregate_protocol import (
    ProjectCostAggregateRepository,
)
from synthorg.persistence.project_cost_claim_seen_protocol import (
    ProjectCostClaimSeenRepository,
)
from synthorg.persistence.project_environment_protocol import (
    ProjectEnvironmentRepository,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.project_workspace_protocol import (
    ProjectWorkspaceRepository,
)
from synthorg.persistence.provider_audit_protocol import (
    ProviderAuditRepo,
)
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventRepository,
)
from synthorg.persistence.red_team_report_protocol import (
    RedTeamReportArchiveRepository,
)
from synthorg.persistence.research_protocol import (
    ResearchRunRepository,
)
from synthorg.persistence.resume_intent_protocol import (
    ResumeIntentRepository,
)
from synthorg.persistence.risk_override_protocol import (
    RiskOverrideRepository,
)
from synthorg.persistence.seen_claims_protocol import (
    SeenClaimsRepository,
)
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.persistence.ssrf_violation_protocol import (
    SsrfViolationRepository,
)
from synthorg.persistence.subworkflow_protocol import (
    SubworkflowRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.persistence.user_protocol import (
    ApiKeyRepository,
    UserRepository,
)
from synthorg.persistence.version_protocol import (
    VersionRepository,
)
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionRepository,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionRepository,
)
from synthorg.versioning.service import (
    VersioningService,
)


class PersistenceBackendKind(StrEnum):
    """Discriminator for the active persistence backend.

    A ``StrEnum`` so existing call sites that key a dispatch table by the
    raw string (``builders.get(backend.kind)``) keep working: each member
    hashes and compares equal to its string value.
    """

    SQLITE = "sqlite"
    POSTGRES = "postgres"


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
        lifecycle_transitions: Repository for plan and project status changes.
        task_metrics: Repository for TaskMetricRecord persistence.
        parked_contexts: Repository for ParkedContext persistence.
        resume_intents: Repository for in-flight approval resume intents.
        audit_entries: Repository for AuditEntry persistence.
        users: Repository for User persistence.
        api_keys: Repository for ApiKey persistence.
        checkpoints: Repository for Checkpoint persistence.
        heartbeats: Repository for Heartbeat persistence.
        agent_states: Repository for AgentRuntimeState persistence.
        settings: Repository for namespaced settings persistence.
        artifacts: Repository for Artifact persistence.
        projects: Repository for Project persistence.
        plans: Repository for durable Plan persistence.
        project_docs: Repository for living-documentation metadata persistence.
        knowledge_sources: Repository for the knowledge-source registry.
        knowledge_provenance: Repository for per-chunk knowledge provenance.
        deliverable_receipts: Repository for deliverable provenance receipts.
        knowledge_usage_records: Repository for per-run knowledge-usage capture.
        code_execution_records: Repository for per-run code-execution capture.
        evaluation_reports: Repository for the evaluate stage's verdicts.
        workflow_definitions: Repository for workflow definition persistence.
        workflow_executions: Repository for workflow execution persistence.
        workflow_versions: Repository for workflow definition version
            snapshot persistence.
        identity_versions: Repository for AgentIdentity version snapshot
            persistence.
        budget_config_versions: Repository for BudgetConfig version snapshot
            persistence.
        company_versions: Repository for Company version snapshot persistence.
        role_versions: Repository for Role version snapshot persistence.
        decision_records: Repository for DecisionRecord persistence
            (auditable approval-gate decisions drop-box).
        risk_overrides: Repository for RiskTierOverride persistence.
        ssrf_violations: Repository for SsrfViolation persistence.
        model_tool_call_signals: Repository for runtime tool-call failure
            signal persistence (decay accumulator).
        model_capability_scores: Repository for published per-axis capability
            measurements, one row per (source, model, axis).
        capability_source_statuses: Repository for per-source ingest
            outcomes, which is what says whether a source still answers.
        provider_failover_events: Repository for dispatches an operator's
            declared alternate served, so which connection actually answered
            survives the restart the log does not.
        connections: Repository for external service connection
            persistence.
        connection_secrets: Repository for encrypted connection secret
            persistence.
        oauth_states: Repository for transient OAuth authorization
            state persistence.
        webhook_receipts: Repository for webhook receipt log
            persistence.
        idempotency_keys: Repository for persistent idempotency keys
            -- atomic claim/complete/fail primitive shared by webhook
            receivers, the backup endpoint, and any other retry-prone
            surface that needs cross-restart deduplication.
        custom_rules: Repository for custom signal rule persistence.
    """

    @property
    def kind(self) -> PersistenceBackendKind:
        """Return the backend's discriminator.

        One of :class:`PersistenceBackendKind`. Used by call sites that
        need to pick a backend-specific helper (e.g. backup handler
        factories) without ``isinstance`` checks. The enum type means
        mypy rejects an implementation that returns any other value, and
        because it is a ``StrEnum`` it still keys string-keyed dispatch
        tables.
        """
        ...

    @property
    def config(self) -> SQLiteConfig | PostgresConfig:
        """Return the connection details this backend was built from.

        The backup-handler factory needs the database it is actually
        pointed at, which is not recoverable from ``RootConfig``: an
        env-driven boot (``SYNTHORG_DATABASE_URL``) parses its own
        config in ``api/boot_persistence`` and leaves
        ``RootConfig.persistence`` describing the operator's declared
        intent instead. Pairing this with :attr:`kind` means the
        discriminator and the connection details always come from one
        object and cannot disagree.
        """
        ...

    @property
    def supports_conversational_approvals(self) -> bool:
        """Whether this backend can durably persist conversational approvals.

        ``True`` for backends that can durably retain a parked approval
        produced mid-conversation (Chief of Staff propose / invite). Both
        SQLite and Postgres carry the conversational tables and the
        ``approvals.source`` CHECK that admit these rows, so both return
        ``True``. Wiring guards read this capability predicate rather than
        comparing ``kind``/``backend_name`` against a literal.
        """
        ...

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

    def get_db(self) -> object:
        """Return the underlying database connection.

        Returns:
            The raw database connection object (backend-specific).

        Raises:
            PersistenceConnectionError: If not yet connected.
        """
        ...

    def write_context(self) -> AbstractAsyncContextManager[None]:
        """Async context manager around mutating SQL on this backend.

        The mutual-exclusion guarantee is backend-specific:

        - **SQLite** acquires a shared in-process write lock so that
          multi-statement transactions on the single
          ``aiosqlite.Connection`` cannot interleave at the statement
          level. Concurrent writers on the same backend instance
          serialize.
        - **Postgres** yields immediately: each repository operation
          checks out an independent connection from the async pool, so
          writers are already isolated at the database level. The
          method exists on this backend only to keep the cross-backend
          interface uniform; it does not provide mutual exclusion
          beyond what the pool already gives.

        Use it in repository write paths so the same code path works
        on both backends::

            async with backend.write_context():
                await db.execute(...)
                await db.commit()

        Each call returns a fresh context manager. On SQLite, the
        underlying lock primitive is shared across calls so concurrent
        callers serialize. On Postgres, there is no shared primitive.
        Repositories are wired with this method (as a callable) at
        backend construction; callers that already hold a
        ``PersistenceBackend`` reference can use it directly for
        cross-repo transactional boundaries.

        Callers must not rely on ``write_context`` for distributed
        mutual exclusion or cross-backend serializability.
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
    def lifecycle_transitions(self) -> LifecycleTransitionRepository:
        """Repository for plan and project status-transition records."""
        ...

    @property
    def deleted_entities(self) -> DeletedEntityRepository:
        """Repository recording what a deleted task, plan or project was."""
        ...

    @property
    def task_metrics(self) -> TaskMetricRepository:
        """Repository for TaskMetricRecord persistence."""
        ...

    @property
    def parked_contexts(self) -> ParkedContextRepository:
        """Repository for ParkedContext persistence."""
        ...

    @property
    def resume_intents(self) -> ResumeIntentRepository:
        """Repository for in-flight approval resume intents."""
        ...

    @property
    def audit_entries(self) -> AuditRepository:
        """Repository for AuditEntry persistence."""
        ...

    @property
    def provider_audit_events(self) -> ProviderAuditRepo:
        """Repository for the provider mutation audit log."""
        ...

    @property
    def preset_overrides(self) -> PresetOverrideRepo:
        """Repository for operator-authored provider preset overrides."""
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
    def flight_recorder_frames(self) -> FlightRecorderFrameRepository:
        """Repository for flight-recorder frame persistence."""
        ...

    @property
    def red_team_reports(self) -> RedTeamReportArchiveRepository:
        """Repository for the durable red-team report archive."""
        ...

    @property
    def completion_oracle_reports(self) -> CompletionOracleReportArchiveRepository:
        """Repository for the durable completion-oracle verdict archive."""
        ...

    @property
    def deliverable_receipts(self) -> DeliverableReceiptRepository:
        """Repository for deliverable provenance-receipt persistence."""
        ...

    @property
    def knowledge_usage_records(self) -> KnowledgeUsageRecordRepository:
        """Repository for per-run knowledge-usage capture persistence."""
        ...

    @property
    def code_execution_records(self) -> CodeExecutionRecordRepository:
        """Repository for per-run code-execution (test) capture persistence."""
        ...

    @property
    def evaluation_reports(self) -> EvaluationReportRepository:
        """Repository for the evaluate stage's per-initiative verdicts."""
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
    def plans(self) -> PlanRepository:
        """Repository for durable Plan persistence (reviewable decompositions)."""
        ...

    @property
    def plan_comments(self) -> PlanItemCommentRepository:
        """Repository for per-item plan comment threads."""
        ...

    @property
    def project_workspaces(self) -> ProjectWorkspaceRepository:
        """Repository for persistent per-project workspace mappings."""
        ...

    @property
    def codebase_structure_maps(self) -> CodebaseStructureMapRepository:
        """Repository for per-project brownfield codebase structure maps."""
        ...

    @property
    def project_environments(self) -> ProjectEnvironmentRepository:
        """Repository for persistent per-project environment mappings."""
        ...

    @property
    def project_docs(self) -> DocsRepository:
        """Repository for living-documentation metadata persistence."""
        ...

    @property
    def project_brain(self) -> ProjectBrainRepository:
        """Repository for the long-horizon project-brain store."""
        ...

    @property
    def knowledge_sources(self) -> KnowledgeSourceRepository:
        """Repository for the knowledge-source registry."""
        ...

    @property
    def knowledge_provenance(self) -> ChunkProvenanceRepository:
        """Repository for per-chunk knowledge provenance."""
        ...

    @property
    def research_runs(self) -> ResearchRunRepository:
        """Repository for the research-run record."""
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
    def model_tool_call_signals(self) -> ModelToolCallSignalRepository:
        """Repository for runtime tool-call failure signal persistence."""
        ...

    @property
    def model_capability_scores(self) -> ModelCapabilityScoreRepository:
        """Repository for published per-axis capability measurements."""
        ...

    @property
    def capability_source_statuses(self) -> CapabilitySourceStatusRepository:
        """Repository for per-source capability-ingest outcomes."""
        ...

    @property
    def provider_failover_events(self) -> ProviderFailoverEventRepository:
        """Repository for dispatches served by a declared alternate."""
        ...

    @property
    def tracked_containers(self) -> TrackedContainerRepository:
        """Repository for Docker sandbox tracked-container records."""
        ...

    @property
    def background_jobs(self) -> BackgroundJobRepository:
        """Repository for backgrounded shell job records."""
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
        """Repository for persistent idempotency keys."""
        ...

    @property
    def seen_claims(self) -> SeenClaimsRepository:
        """Repository for worker TaskClaim dedup persistence."""
        ...

    @property
    def project_cost_claim_seen(self) -> ProjectCostClaimSeenRepository:
        """Repository for durable project-cost-claim dedup (restart-safe billing)."""
        ...

    @property
    def principle_overrides(self) -> PrincipleOverrideRepository:
        """Repository for rollback-restored principle overrides."""
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
    def memory_vectors(self) -> MemoryVectorRepository:
        """Repository for durable agent memory with hybrid retrieval."""
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
    def hiring_requests(self) -> HiringRequestRepository:
        """Repository for in-flight hiring requests."""
        ...

    @property
    def agent_contributions(self) -> AgentContributionRepository:
        """Repository for the append-only agent-contribution log."""
        ...

    @property
    def audit_chain_entries(self) -> AuditChainRepository:
        """Repository for the append-only audit hash chain."""
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
