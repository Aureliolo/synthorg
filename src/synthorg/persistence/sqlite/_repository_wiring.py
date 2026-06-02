# module-kind: complex_service
"""Repository instantiation and slot lifecycle for the SQLite backend.

This mixin owns the connection-bound construction of every domain
repository (``_create_repositories``) and the reset of every repository
slot to ``None`` (``_clear_state``). It is the write-side counterpart to
``_BackendRepositoryAccessors`` (the read-side ``@property`` forwarders),
which it extends so the ``_<name>`` slot declarations live in one place.

One cohesive responsibility: wire every persistence repository on the
SQLite backend to the live connection. Like ``_backend_accessors``, the
size scales linearly with the repository count and the construction
contract (``self._db`` + ``self.write_context``) is uniform across them
all; per-domain sibling mixins would fragment the construction invariant
across files without reducing total LOC. Collecting the wiring here keeps
``backend.py`` focused on the connection lifecycle (connect / disconnect
/ migrate / health) rather than the ~270-line mechanical instantiation
block.
"""

import json
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
from synthorg.core.role import Role
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.persistence.sqlite._backend_accessors import (
    _BackendRepositoryAccessors,
)
from synthorg.persistence.sqlite.agent_state_repo import (
    SQLiteAgentStateRepository,
)
from synthorg.persistence.sqlite.artifact_repo import (
    SQLiteArtifactRepository,
)
from synthorg.persistence.sqlite.audit_repository import (
    SQLiteAuditRepository,
)
from synthorg.persistence.sqlite.ceremony_scheduler_state_repo import (
    SQLiteCeremonySchedulerStateRepository,
)
from synthorg.persistence.sqlite.checkpoint_repo import (
    SQLiteCheckpointRepository,
)
from synthorg.persistence.sqlite.circuit_breaker_repo import (
    SQLiteCircuitBreakerStateRepository,
)
from synthorg.persistence.sqlite.code_execution_repo import (
    SQLiteCodeExecutionRecordRepository,
)
from synthorg.persistence.sqlite.codebase_structure_map_repo import (
    SQLiteCodebaseStructureMapRepository,
)
from synthorg.persistence.sqlite.connection_repo import SQLiteConnectionRepository
from synthorg.persistence.sqlite.connection_secret_repo import (
    SQLiteConnectionSecretRepository,
)
from synthorg.persistence.sqlite.cost_record_repo import (
    SQLiteCostRecordRepository,
)
from synthorg.persistence.sqlite.custom_rule_repo import (
    SQLiteCustomRuleRepository,
)
from synthorg.persistence.sqlite.decision import (
    SQLiteDecisionRepository,
)
from synthorg.persistence.sqlite.deliverable_receipt_repo import (
    SQLiteDeliverableReceiptRepository,
)
from synthorg.persistence.sqlite.docs_repo import SQLiteDocsRepository
from synthorg.persistence.sqlite.fine_tune_repo import (
    SQLiteFineTuneCheckpointRepository,
    SQLiteFineTuneRunRepository,
)
from synthorg.persistence.sqlite.flight_recorder_repo import (
    SQLiteFlightRecorderFrameRepository,
)
from synthorg.persistence.sqlite.heartbeat_repo import (
    SQLiteHeartbeatRepository,
)
from synthorg.persistence.sqlite.hr_repositories import (
    SQLiteCollaborationMetricRepository,
    SQLiteLifecycleEventRepository,
    SQLiteTaskMetricRepository,
)
from synthorg.persistence.sqlite.idempotency_repo import (
    SQLiteIdempotencyRepository,
)
from synthorg.persistence.sqlite.knowledge_provenance_repo import (
    SQLiteChunkProvenanceRepository,
)
from synthorg.persistence.sqlite.knowledge_source_repo import (
    SQLiteKnowledgeSourceRepository,
)
from synthorg.persistence.sqlite.knowledge_usage_repo import (
    SQLiteKnowledgeUsageRecordRepository,
)
from synthorg.persistence.sqlite.lockout_repo import (
    SQLiteLockoutRepository,
)
from synthorg.persistence.sqlite.mcp_installation_repo import (
    SQLiteMcpInstallationRepository,
)
from synthorg.persistence.sqlite.meeting_cooldown_repo import (
    SQLiteMeetingCooldownRepository,
)
from synthorg.persistence.sqlite.message_repo import (
    SQLiteMessageRepository,
)
from synthorg.persistence.sqlite.oauth_state_repo import SQLiteOAuthStateRepository
from synthorg.persistence.sqlite.ontology_drift_repo import (
    SQLiteOntologyDriftReportRepository,
)
from synthorg.persistence.sqlite.ontology_entity_repo import (
    SQLiteOntologyEntityRepository,
)
from synthorg.persistence.sqlite.org_fact_repo import (
    SQLiteOrgFactRepository,
)
from synthorg.persistence.sqlite.parked_context_repo import (
    SQLiteParkedContextRepository,
)
from synthorg.persistence.sqlite.preset_override_repo import (
    SQLitePresetOverrideRepo,
)
from synthorg.persistence.sqlite.preset_repo import (
    SQLitePersonalityPresetRepository,
)
from synthorg.persistence.sqlite.principle_override_repo import (
    SQLitePrincipleOverrideRepository,
)
from synthorg.persistence.sqlite.project_brain_repo import (
    SQLiteProjectBrainRepository,
)
from synthorg.persistence.sqlite.project_cost_aggregate_repo import (
    SQLiteProjectCostAggregateRepository,
)
from synthorg.persistence.sqlite.project_environment_repo import (
    SQLiteProjectEnvironmentRepository,
)
from synthorg.persistence.sqlite.project_repo import (
    SQLiteProjectRepository,
)
from synthorg.persistence.sqlite.project_workspace_repo import (
    SQLiteProjectWorkspaceRepository,
)
from synthorg.persistence.sqlite.provider_audit_repo import (
    SQLiteProviderAuditRepo,
)
from synthorg.persistence.sqlite.refresh_repo import (
    SQLiteRefreshTokenRepository,
)
from synthorg.persistence.sqlite.research_run_repo import (
    SQLiteResearchRunRepository,
)
from synthorg.persistence.sqlite.risk_override_repo import (
    SQLiteRiskOverrideRepository,
)
from synthorg.persistence.sqlite.seen_claims_repo import (
    SQLiteSeenClaimsRepository,
)
from synthorg.persistence.sqlite.session_repo import (
    SQLiteSessionRepository,
)
from synthorg.persistence.sqlite.settings_repo import (
    SQLiteSettingsRepository,
)
from synthorg.persistence.sqlite.ssrf_violation_repo import (
    SQLiteSsrfViolationRepository,
)
from synthorg.persistence.sqlite.subworkflow_repo import (
    SQLiteSubworkflowRepository,
)
from synthorg.persistence.sqlite.task_repo import (
    SQLiteTaskRepository,
)
from synthorg.persistence.sqlite.tracked_container_repo import (
    SQLiteTrackedContainerRepository,
)
from synthorg.persistence.sqlite.training_plan_repo import (
    SQLiteTrainingPlanRepository,
)
from synthorg.persistence.sqlite.training_result_repo import (
    SQLiteTrainingResultRepository,
)
from synthorg.persistence.sqlite.user_repo import (
    SQLiteApiKeyRepository,
    SQLiteUserRepository,
)
from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.persistence.sqlite.webhook_receipt_repo import (
    SQLiteWebhookReceiptRepository,
)
from synthorg.persistence.sqlite.workflow_definition_repo import (
    SQLiteWorkflowDefinitionRepository,
)
from synthorg.persistence.sqlite.workflow_execution_repo import (
    SQLiteWorkflowExecutionRepository,
)
from synthorg.persistence.version_protocol import VersionRepository

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager


class _SQLiteRepositoryWiring(_BackendRepositoryAccessors):
    """Mixin: construct and reset every SQLite repository slot.

    The hosting class (``SQLitePersistenceBackend``) provides the live
    ``_db`` connection and the ``write_context`` write-serialisation
    seam; this mixin populates every ``_<name>`` slot from them in
    ``_create_repositories`` and resets them in ``_clear_state``.
    """

    _db: aiosqlite.Connection | None
    # Cached lockout repository -- in-memory cache must survive across
    # ``build_lockouts`` calls, otherwise ``is_locked`` is always False
    # on a freshly-built instance. Cleared on ``disconnect`` via
    # ``_clear_state``.
    _lockouts: SQLiteLockoutRepository | None

    if TYPE_CHECKING:

        def write_context(self) -> AbstractAsyncContextManager[None]:
            """Write-serialisation seam provided by the hosting backend."""
            ...

    def _clear_state(self) -> None:
        """Reset connection and repository references to ``None``."""
        self._db = None
        self._artifacts = None
        self._projects = None
        self._project_workspaces = None
        self._codebase_structure_maps = None
        self._project_environments = None
        self._project_docs = None
        self._project_brain = None
        self._knowledge_sources = None
        self._knowledge_provenance = None
        self._research_runs = None
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
        self._deliverable_receipts = None
        self._knowledge_usage_records = None
        self._code_execution_records = None
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
        self._fine_tune_checkpoints = None
        self._fine_tune_runs = None
        self._training_plans = None
        self._training_results = None
        self._custom_rules = None
        self._sessions = None
        self._refresh_tokens = None
        self._idempotency_keys = None
        self._seen_claims = None
        self._principle_overrides = None
        self._mcp_installations = None
        self._org_facts = None
        self._ontology_entities = None
        self._ontology_drift = None
        self._lockouts = None
        self._connections = None
        self._connection_secrets = None
        self._oauth_states = None
        self._webhook_receipts = None

    def _create_repositories(self) -> None:
        """Instantiate all repository objects from the active connection."""
        assert self._db is not None  # noqa: S101
        self._artifacts = SQLiteArtifactRepository(
            self._db,
            write_context=self.write_context,
        )
        self._projects = SQLiteProjectRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_workspaces = SQLiteProjectWorkspaceRepository(
            self._db,
            write_context=self.write_context,
        )
        self._codebase_structure_maps = SQLiteCodebaseStructureMapRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_environments = SQLiteProjectEnvironmentRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_docs = SQLiteDocsRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_brain = SQLiteProjectBrainRepository(
            self._db,
            write_context=self.write_context,
        )
        self._knowledge_sources = SQLiteKnowledgeSourceRepository(
            self._db,
            write_context=self.write_context,
        )
        self._knowledge_provenance = SQLiteChunkProvenanceRepository(
            self._db,
            write_context=self.write_context,
        )
        self._research_runs = SQLiteResearchRunRepository(
            self._db,
            write_context=self.write_context,
        )
        self._tasks = SQLiteTaskRepository(
            self._db,
            write_context=self.write_context,
        )
        self._cost_records = SQLiteCostRecordRepository(
            self._db,
            write_context=self.write_context,
        )
        self._messages = SQLiteMessageRepository(
            self._db,
            write_context=self.write_context,
        )
        self._lifecycle_events = SQLiteLifecycleEventRepository(
            self._db,
            write_context=self.write_context,
        )
        self._task_metrics = SQLiteTaskMetricRepository(
            self._db,
            write_context=self.write_context,
        )
        self._collaboration_metrics = SQLiteCollaborationMetricRepository(
            self._db,
            write_context=self.write_context,
        )
        self._parked_contexts = SQLiteParkedContextRepository(
            self._db,
            write_context=self.write_context,
        )
        self._audit_entries = SQLiteAuditRepository(
            self._db,
            write_context=self.write_context,
        )
        self._provider_audit_events = SQLiteProviderAuditRepo(
            self._db,
            write_context=self.write_context,
        )
        self._preset_overrides = SQLitePresetOverrideRepo(
            self._db,
            write_context=self.write_context,
        )
        self._users = SQLiteUserRepository(
            self._db,
            write_context=self.write_context,
        )
        self._api_keys = SQLiteApiKeyRepository(
            self._db,
            write_context=self.write_context,
        )
        self._checkpoints = SQLiteCheckpointRepository(
            self._db,
            write_context=self.write_context,
        )
        self._flight_recorder_frames = SQLiteFlightRecorderFrameRepository(
            self._db,
            write_context=self.write_context,
        )
        self._deliverable_receipts = SQLiteDeliverableReceiptRepository(
            self._db,
            write_context=self.write_context,
        )
        self._knowledge_usage_records = SQLiteKnowledgeUsageRecordRepository(
            self._db,
            write_context=self.write_context,
        )
        self._code_execution_records = SQLiteCodeExecutionRecordRepository(
            self._db,
            write_context=self.write_context,
        )
        self._heartbeats = SQLiteHeartbeatRepository(
            self._db,
            write_context=self.write_context,
        )
        self._agent_states = SQLiteAgentStateRepository(
            self._db,
            write_context=self.write_context,
        )
        self._settings = SQLiteSettingsRepository(
            self._db,
            write_context=self.write_context,
        )
        self._custom_presets = SQLitePersonalityPresetRepository(
            self._db,
            write_context=self.write_context,
        )
        self._workflow_definitions = SQLiteWorkflowDefinitionRepository(
            self._db,
            write_context=self.write_context,
        )
        self._workflow_executions = SQLiteWorkflowExecutionRepository(
            self._db,
            write_context=self.write_context,
        )
        self._subworkflows = SQLiteSubworkflowRepository(
            self._db,
            write_context=self.write_context,
        )

        def _ver_repo[T: BaseModel](
            table: str,
            model_cls: type[T],
        ) -> VersionRepository[T]:
            assert self._db is not None  # noqa: S101
            return SQLiteVersionRepository(
                self._db,
                table_name=table,
                serialize_snapshot=lambda m: json.dumps(
                    m.model_dump(mode="json"),
                ),
                deserialize_snapshot=lambda s: model_cls.model_validate(
                    json.loads(s),
                ),
                write_context=self.write_context,
            )

        self._workflow_versions = _ver_repo(
            "workflow_definition_versions",
            WorkflowDefinition,
        )
        self._identity_versions = _ver_repo(
            "agent_identity_versions",
            AgentIdentity,
        )
        self._evaluation_config_versions = _ver_repo(
            "evaluation_config_versions",
            EvaluationConfig,
        )
        self._budget_config_versions = _ver_repo(
            "budget_config_versions",
            BudgetConfig,
        )
        self._company_versions = _ver_repo(
            "company_versions",
            Company,
        )
        self._role_versions = _ver_repo(
            "role_versions",
            Role,
        )
        self._decision_records = SQLiteDecisionRepository(
            self._db, write_context=self.write_context
        )
        self._risk_overrides = SQLiteRiskOverrideRepository(
            self._db,
            write_context=self.write_context,
        )
        self._ssrf_violations = SQLiteSsrfViolationRepository(
            self._db,
            write_context=self.write_context,
        )
        self._circuit_breaker_state = SQLiteCircuitBreakerStateRepository(
            self._db,
            write_context=self.write_context,
        )
        self._ceremony_scheduler_state = SQLiteCeremonySchedulerStateRepository(
            self._db,
            write_context=self.write_context,
        )
        self._meeting_cooldown = SQLiteMeetingCooldownRepository(
            self._db,
            write_context=self.write_context,
        )
        self._tracked_containers = SQLiteTrackedContainerRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_cost_aggregates = SQLiteProjectCostAggregateRepository(
            self._db,
            write_context=self.write_context,
        )
        self._fine_tune_checkpoints = SQLiteFineTuneCheckpointRepository(
            self._db,
            write_context=self.write_context,
        )
        self._fine_tune_runs = SQLiteFineTuneRunRepository(
            self._db,
            write_context=self.write_context,
        )
        self._training_plans = SQLiteTrainingPlanRepository(
            self._db,
            write_context=self.write_context,
        )
        self._training_results = SQLiteTrainingResultRepository(
            self._db,
            write_context=self.write_context,
        )
        self._custom_rules = SQLiteCustomRuleRepository(
            self._db,
            write_context=self.write_context,
        )
        self._sessions = SQLiteSessionRepository(
            self._db,
            write_context=self.write_context,
        )
        self._refresh_tokens = SQLiteRefreshTokenRepository(
            self._db,
            write_context=self.write_context,
        )
        self._idempotency_keys = SQLiteIdempotencyRepository(
            self._db,
            write_context=self.write_context,
        )
        self._seen_claims = SQLiteSeenClaimsRepository(
            self._db,
            write_context=self.write_context,
        )
        self._principle_overrides = SQLitePrincipleOverrideRepository(
            self._db,
            write_context=self.write_context,
        )
        self._mcp_installations = SQLiteMcpInstallationRepository(
            self._db,
            write_context=self.write_context,
        )
        self._org_facts = SQLiteOrgFactRepository(
            self._db,
            write_context=self.write_context,
        )
        self._ontology_entities = SQLiteOntologyEntityRepository(
            self._db,
            write_context=self.write_context,
        )
        self._ontology_drift = SQLiteOntologyDriftReportRepository(
            self._db,
            write_context=self.write_context,
        )
        self._connections = SQLiteConnectionRepository(
            self._db,
            write_context=self.write_context,
        )
        self._connection_secrets = SQLiteConnectionSecretRepository(
            self._db,
            write_context=self.write_context,
        )
        self._oauth_states = SQLiteOAuthStateRepository(
            self._db,
            write_context=self.write_context,
        )
        self._webhook_receipts = SQLiteWebhookReceiptRepository(
            self._db,
            write_context=self.write_context,
        )
