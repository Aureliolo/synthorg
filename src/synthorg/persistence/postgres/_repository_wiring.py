# module-kind: complex_service
"""Repository instantiation and slot lifecycle for the Postgres backend.

Owns the pool-bound construction of every domain repository
(``_create_repositories``) and the reset of every repository slot to
``None`` (``_clear_state``). Write-side counterpart to
``_PostgresBackendRepositoryAccessors``, which it extends so the
``_<name>`` slot declarations live in one place.

One cohesive responsibility: wire every persistence repository on the
Postgres backend to the shared connection pool. The size scales
linearly with the repository count and the construction contract
(``self._pool``) is uniform across them all.
"""

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.persistence.postgres._backend_accessors import (
    _PostgresBackendRepositoryAccessors,
)
from synthorg.persistence.postgres.agent_state_repo import (
    PostgresAgentStateRepository,
)
from synthorg.persistence.postgres.artifact_repo import PostgresArtifactRepository
from synthorg.persistence.postgres.audit_repository import PostgresAuditRepository
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
from synthorg.persistence.postgres.cost_record_repo import (
    PostgresCostRecordRepository,
)
from synthorg.persistence.postgres.custom_rule_repo import (
    PostgresCustomRuleRepository,
)
from synthorg.persistence.postgres.decision import PostgresDecisionRepository
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
from synthorg.persistence.postgres.mcp_installation_repo import (
    PostgresMcpInstallationRepository,
)
from synthorg.persistence.postgres.meeting_cooldown_repo import (
    PostgresMeetingCooldownRepository,
)
from synthorg.persistence.postgres.message_repo import (
    PostgresMessageRepository,
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
from synthorg.persistence.postgres.project_brain_repo import (
    PostgresProjectBrainRepository,
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
from synthorg.persistence.postgres.task_repo import (
    PostgresTaskRepository,
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


class _PostgresRepositoryWiring(_PostgresBackendRepositoryAccessors):
    """Mixin: construct and reset every Postgres repository slot.

    The hosting class (``PostgresPersistenceBackend``) provides the
    live ``_pool``; this mixin populates every ``_<name>`` slot from
    it in ``_create_repositories`` and resets them in ``_clear_state``.
    """

    _pool: AsyncConnectionPool | None

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
        self._project_brain = None
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
        """Instantiate all repository objects from the active pool.

        Raises:
            PersistenceConnectionError: If called before the backend's
                connection pool has been established.
        """
        if self._pool is None:
            msg = "Not connected -- call connect() before creating repositories"
            raise PersistenceConnectionError(msg)
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
        self._project_brain = PostgresProjectBrainRepository(pool)
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
