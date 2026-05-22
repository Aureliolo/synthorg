"""SQLite persistence backend implementation."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import aiosqlite
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_ALREADY_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTING,
    PERSISTENCE_BACKEND_CONNECTION_FAILED,
    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
    PERSISTENCE_BACKEND_DISCONNECTED,
    PERSISTENCE_BACKEND_DISCONNECTING,
    PERSISTENCE_BACKEND_HEALTH_CHECK,
    PERSISTENCE_BACKEND_NOT_CONNECTED,
    PERSISTENCE_BACKEND_WAL_MODE_FAILED,
)
from synthorg.persistence import migrations
from synthorg.persistence._shared import format_iso_utc
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
from synthorg.persistence.sqlite.connection_repo import SQLiteConnectionRepository
from synthorg.persistence.sqlite.connection_secret_repo import (
    SQLiteConnectionSecretRepository,
)
from synthorg.persistence.sqlite.custom_rule_repo import (
    SQLiteCustomRuleRepository,
)
from synthorg.persistence.sqlite.decision_repo import (
    SQLiteDecisionRepository,
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
from synthorg.persistence.sqlite.lockout_repo import (
    SQLiteLockoutRepository,
)
from synthorg.persistence.sqlite.mcp_installation_repo import (
    SQLiteMcpInstallationRepository,
)
from synthorg.persistence.sqlite.meeting_cooldown_repo import (
    SQLiteMeetingCooldownRepository,
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
from synthorg.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
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

if TYPE_CHECKING:
    from synthorg.core.auth.config import AuthConfig
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.auth_protocol import LockoutRepository
    from synthorg.persistence.config import SQLiteConfig
    from synthorg.persistence.escalation_protocol import EscalationQueueRepository
    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class SQLitePersistenceBackend(_BackendRepositoryAccessors):
    """SQLite implementation of the PersistenceBackend protocol.

    Uses a single ``aiosqlite.Connection`` with WAL mode enabled by
    default for file-based databases (in-memory databases do not
    support WAL).  Configurable via ``SQLiteConfig.wal_mode``.

    Args:
        config: SQLite-specific configuration.
    """

    def __init__(self, config: SQLiteConfig) -> None:  # noqa: PLR0915 -- repo registry setup intentionally enumerates every attribute
        self._config = config
        self._lifecycle_lock = asyncio.Lock()
        # Serializes multi-statement transactions on the single
        # aiosqlite connection. Exposed to repos via ``write_context``.
        self._write_lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None
        self._artifacts: SQLiteArtifactRepository | None = None
        self._projects: SQLiteProjectRepository | None = None
        self._project_workspaces: SQLiteProjectWorkspaceRepository | None = None
        self._project_environments: SQLiteProjectEnvironmentRepository | None = None
        self._project_docs: SQLiteDocsRepository | None = None
        self._knowledge_sources: SQLiteKnowledgeSourceRepository | None = None
        self._knowledge_provenance: SQLiteChunkProvenanceRepository | None = None
        self._research_runs: SQLiteResearchRunRepository | None = None
        self._tasks: SQLiteTaskRepository | None = None
        self._cost_records: SQLiteCostRecordRepository | None = None
        self._messages: SQLiteMessageRepository | None = None
        self._lifecycle_events: SQLiteLifecycleEventRepository | None = None
        self._task_metrics: SQLiteTaskMetricRepository | None = None
        self._collaboration_metrics: SQLiteCollaborationMetricRepository | None = None
        self._parked_contexts: SQLiteParkedContextRepository | None = None
        self._audit_entries: SQLiteAuditRepository | None = None
        self._provider_audit_events: SQLiteProviderAuditRepo | None = None
        self._preset_overrides: SQLitePresetOverrideRepo | None = None
        self._users: SQLiteUserRepository | None = None
        self._api_keys: SQLiteApiKeyRepository | None = None
        self._checkpoints: SQLiteCheckpointRepository | None = None
        self._flight_recorder_frames: SQLiteFlightRecorderFrameRepository | None = None
        self._heartbeats: SQLiteHeartbeatRepository | None = None
        self._agent_states: SQLiteAgentStateRepository | None = None
        self._settings: SQLiteSettingsRepository | None = None
        self._custom_presets: SQLitePersonalityPresetRepository | None = None
        self._workflow_definitions: SQLiteWorkflowDefinitionRepository | None = None
        self._workflow_executions: SQLiteWorkflowExecutionRepository | None = None
        self._subworkflows: SQLiteSubworkflowRepository | None = None
        self._workflow_versions: VersionRepository[WorkflowDefinition] | None = None
        self._identity_versions: VersionRepository[AgentIdentity] | None = None
        self._evaluation_config_versions: VersionRepository[EvaluationConfig] | None = (
            None
        )
        self._budget_config_versions: VersionRepository[BudgetConfig] | None = None
        self._company_versions: VersionRepository[Company] | None = None
        self._role_versions: VersionRepository[Role] | None = None
        self._decision_records: SQLiteDecisionRepository | None = None
        self._risk_overrides: SQLiteRiskOverrideRepository | None = None
        self._ssrf_violations: SQLiteSsrfViolationRepository | None = None
        self._circuit_breaker_state: SQLiteCircuitBreakerStateRepository | None = None
        self._ceremony_scheduler_state: (
            SQLiteCeremonySchedulerStateRepository | None
        ) = None
        self._meeting_cooldown: SQLiteMeetingCooldownRepository | None = None
        self._tracked_containers: SQLiteTrackedContainerRepository | None = None
        self._project_cost_aggregates: SQLiteProjectCostAggregateRepository | None = (
            None
        )
        self._fine_tune_checkpoints: SQLiteFineTuneCheckpointRepository | None = None
        self._fine_tune_runs: SQLiteFineTuneRunRepository | None = None
        self._training_plans: SQLiteTrainingPlanRepository | None = None
        self._training_results: SQLiteTrainingResultRepository | None = None
        self._custom_rules: SQLiteCustomRuleRepository | None = None
        self._sessions: SQLiteSessionRepository | None = None
        self._refresh_tokens: SQLiteRefreshTokenRepository | None = None
        self._idempotency_keys: SQLiteIdempotencyRepository | None = None
        self._seen_claims: SQLiteSeenClaimsRepository | None = None
        self._principle_overrides: SQLitePrincipleOverrideRepository | None = None
        self._mcp_installations: SQLiteMcpInstallationRepository | None = None
        self._org_facts: SQLiteOrgFactRepository | None = None
        self._ontology_entities: SQLiteOntologyEntityRepository | None = None
        self._ontology_drift: SQLiteOntologyDriftReportRepository | None = None
        # Cached lockout repository -- in-memory cache must survive
        # across ``build_lockouts`` calls, otherwise ``is_locked`` is
        # always False on a freshly-built instance.
        self._lockouts: SQLiteLockoutRepository | None = None
        self._connections: SQLiteConnectionRepository | None = None
        self._connection_secrets: SQLiteConnectionSecretRepository | None = None
        self._oauth_states: SQLiteOAuthStateRepository | None = None
        self._webhook_receipts: SQLiteWebhookReceiptRepository | None = None

    def _clear_state(self) -> None:  # noqa: PLR0915 -- repo registry reset intentionally enumerates every attribute
        """Reset connection and repository references to ``None``."""
        self._db = None
        self._artifacts = None
        self._projects = None
        self._project_workspaces = None
        self._project_environments = None
        self._project_docs = None
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

    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        """Return the backend discriminator (``"sqlite"``)."""
        return "sqlite"

    @property
    def config(self) -> SQLiteConfig:
        """Public read-only view of the backend's config.

        Exposed so callers that need backend-specific details (the
        backup-handler factory walks the path; tests assert against
        the resolved sqlite path) do not have to reach for the
        private ``_config`` attribute.
        """
        return self._config

    async def connect(self) -> None:
        """Open the SQLite database and configure WAL mode."""
        async with self._lifecycle_lock:
            if self._db is not None:
                logger.debug(PERSISTENCE_BACKEND_ALREADY_CONNECTED)
                return

            logger.info(
                PERSISTENCE_BACKEND_CONNECTING,
                path=self._config.path,
            )
            try:
                self._db = await aiosqlite.connect(self._config.path)
                self._db.row_factory = aiosqlite.Row

                # Enable foreign key enforcement (off by default in SQLite).
                await self._db.execute("PRAGMA foreign_keys = ON")

                if self._config.wal_mode:
                    await self._configure_wal()

                self._create_repositories()
            except (sqlite3.Error, OSError) as exc:
                await self._cleanup_failed_connect(exc)

            logger.info(
                PERSISTENCE_BACKEND_CONNECTED,
                path=self._config.path,
            )

    async def _configure_wal(self) -> None:
        """Configure WAL journal mode and size limit.

        Must only be called when ``self._db`` is not ``None``.
        """
        assert self._db is not None  # noqa: S101
        cursor = await self._db.execute("PRAGMA journal_mode=WAL")
        row = await cursor.fetchone()
        actual_mode = row[0] if row else "unknown"
        if actual_mode != "wal" and self._config.path != ":memory:":
            logger.warning(
                PERSISTENCE_BACKEND_WAL_MODE_FAILED,
                requested="wal",
                actual=actual_mode,
            )
        # PRAGMA does not support parameterized queries;
        # journal_size_limit is validated as int >= 0 by Pydantic.
        limit = int(self._config.journal_size_limit)
        await self._db.execute(f"PRAGMA journal_size_limit={limit}")

    def get_db(self) -> aiosqlite.Connection:
        """Return the shared database connection.

        Raises:
            PersistenceConnectionError: If not yet connected.
        """
        if self._db is None:
            msg = "Database not connected"
            raise PersistenceConnectionError(msg)
        return self._db

    @asynccontextmanager
    async def write_context(self) -> AsyncIterator[None]:
        """Acquire the shared write lock for the lifetime of the block.

        Multi-statement transactions on the single ``aiosqlite.Connection``
        must serialize so a sibling repo's INSERT cannot interleave
        between this repo's INSERT and COMMIT. See
        ``PersistenceBackend.write_context`` for the cross-backend
        contract.
        """
        async with self._write_lock:
            yield

    def _create_repositories(self) -> None:  # noqa: PLR0915
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
        self._project_environments = SQLiteProjectEnvironmentRepository(
            self._db,
            write_context=self.write_context,
        )
        self._project_docs = SQLiteDocsRepository(
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

    async def _cleanup_failed_connect(self, exc: sqlite3.Error | OSError) -> None:
        """Log failure, close partial connection, and raise.

        Raises:
            PersistenceConnectionError: Always.
        """
        logger.warning(
            PERSISTENCE_BACKEND_CONNECTION_FAILED,
            path=self._config.path,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if self._db is not None:
            try:
                await self._db.close()
            except (sqlite3.Error, OSError) as cleanup_exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    path=self._config.path,
                    error=safe_error_description(cleanup_exc),
                    error_type=type(cleanup_exc).__name__,
                    context="cleanup_after_connect_failure",
                )
        self._clear_state()
        msg = "Failed to connect to persistence backend"
        raise PersistenceConnectionError(msg) from exc

    async def disconnect(self) -> None:
        """Close the database connection."""
        async with self._lifecycle_lock:
            if self._db is None:
                return

            logger.info(PERSISTENCE_BACKEND_DISCONNECTING, path=self._config.path)
            try:
                await self._db.close()
                logger.info(
                    PERSISTENCE_BACKEND_DISCONNECTED,
                    path=self._config.path,
                )
            except (sqlite3.Error, OSError) as exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    path=self._config.path,
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                self._clear_state()

    async def health_check(self) -> bool:
        """Check database connectivity."""
        if self._db is None:
            return False
        try:
            cursor = await self._db.execute("SELECT 1")
            row = await cursor.fetchone()
            healthy = row is not None
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_BACKEND_HEALTH_CHECK,
                healthy=False,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return False
        logger.debug(PERSISTENCE_BACKEND_HEALTH_CHECK, healthy=healthy)
        return healthy

    async def migrate(self) -> None:
        """Apply pending schema migrations via yoyo-migrations.

        On failure the backend's repositories are reset so callers
        cannot reuse a half-initialised state machine (mirrors the
        postgres backend's pool-close-on-failure behaviour).

        Raises:
            PersistenceConnectionError: If not connected.
            MigrationError: If migration application fails.
        """
        async with self._lifecycle_lock:
            if self._db is None:
                msg = "Cannot migrate: not connected"
                logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
                raise PersistenceConnectionError(msg)
            db_url = migrations.to_sqlite_url(self._config.path)
            try:
                await migrations.migrate_apply(db_url)
            except BaseException:
                db = self._db
                if db is not None:
                    try:
                        await db.close()
                    except (sqlite3.Error, aiosqlite.Error, OSError) as cleanup_exc:
                        logger.warning(
                            PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                            path=self._config.path,
                            error_type=type(cleanup_exc).__name__,
                            error=safe_error_description(cleanup_exc),
                            context="cleanup_after_migration_failure",
                        )
                self._clear_state()
                raise

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        return self._db is not None

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Return the cached lockout repository (built once per connection).

        The lockout repo maintains a process-local in-memory cache
        (``_locked``) on the auth hot path.  Returning a fresh instance
        on every call would reset that cache and silently "unlock"
        every user.  The cache is cleared on ``disconnect`` via
        ``_clear_state``.  The backend's ``write_context`` is passed
        through so lockout transactions serialize with other
        repositories writing to the same aiosqlite connection.
        """
        if self._lockouts is None:
            self._lockouts = SQLiteLockoutRepository(
                self.get_db(),
                auth_config,
                write_context=self.write_context,
            )
        return self._lockouts

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,  # noqa: ARG002
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository.

        ``notify_channel`` is ignored by SQLite (no cross-instance
        NOTIFY/LISTEN). The backend's ``write_context`` is passed
        through so escalation transactions serialize with other
        repositories writing to the same aiosqlite connection.
        """
        from synthorg.persistence.sqlite.escalation_repo import (  # noqa: PLC0415
            SQLiteEscalationRepository,
        )

        db = self.get_db()
        return SQLiteEscalationRepository(db, write_context=self.write_context)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend."""
        from synthorg.persistence.sqlite.ontology_versioning import (  # noqa: PLC0415
            create_ontology_versioning,
        )

        return create_ontology_versioning(
            self.get_db(),
            write_context=self.write_context,
        )

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.
        """
        result = await self.settings.get((NotBlankStr("_system"), key))
        return result.value if result is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.
        """
        from synthorg.persistence.settings_protocol import SettingRow  # noqa: PLC0415

        updated_at = format_iso_utc(datetime.now(UTC))
        await self.settings.save(
            SettingRow(
                namespace=NotBlankStr("_system"),
                key=key,
                value=value,
                updated_at=updated_at,
            ),
        )
