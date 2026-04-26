"""SQLite persistence backend implementation."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.company import Company
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
from synthorg.persistence import atlas
from synthorg.persistence.errors import PersistenceConnectionError
from synthorg.persistence.integration_stubs import (
    InMemoryConnectionRepository,
    InMemoryConnectionSecretRepository,
    InMemoryOAuthStateRepository,
    InMemoryWebhookReceiptRepository,
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
from synthorg.persistence.sqlite.checkpoint_repo import (
    SQLiteCheckpointRepository,
)
from synthorg.persistence.sqlite.circuit_breaker_repo import (
    SQLiteCircuitBreakerStateRepository,
)
from synthorg.persistence.sqlite.custom_rule_repo import (
    SQLiteCustomRuleRepository,
)
from synthorg.persistence.sqlite.decision_repo import (
    SQLiteDecisionRepository,
)
from synthorg.persistence.sqlite.fine_tune_repo import (
    SQLiteFineTuneCheckpointRepository,
    SQLiteFineTuneRunRepository,
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
from synthorg.persistence.sqlite.lockout_repo import (
    SQLiteLockoutRepository,
)
from synthorg.persistence.sqlite.mcp_installation_repo import (
    SQLiteMcpInstallationRepository,
)
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
from synthorg.persistence.sqlite.preset_repo import (
    SQLitePersonalityPresetRepository,
)
from synthorg.persistence.sqlite.project_cost_aggregate_repo import (
    SQLiteProjectCostAggregateRepository,
)
from synthorg.persistence.sqlite.project_repo import (
    SQLiteProjectRepository,
)
from synthorg.persistence.sqlite.refresh_repo import (
    SQLiteRefreshTokenRepository,
)
from synthorg.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
)
from synthorg.persistence.sqlite.risk_override_repo import (
    SQLiteRiskOverrideRepository,
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
from synthorg.persistence.sqlite.workflow_definition_repo import (
    SQLiteWorkflowDefinitionRepository,
)
from synthorg.persistence.sqlite.workflow_execution_repo import (
    SQLiteWorkflowExecutionRepository,
)

if TYPE_CHECKING:
    from synthorg.api.auth.config import AuthConfig
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.auth_protocol import LockoutRepository
    from synthorg.persistence.config import SQLiteConfig
    from synthorg.persistence.escalation_protocol import EscalationQueueRepository
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
        FineTuneRunRepository,
    )
    from synthorg.persistence.version_repo import VersionRepository
    from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class SQLitePersistenceBackend:
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
        # Shared write lock for multi-statement transactions on the
        # single aiosqlite connection.
        self._shared_write_lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None
        self._artifacts: SQLiteArtifactRepository | None = None
        self._projects: SQLiteProjectRepository | None = None
        self._tasks: SQLiteTaskRepository | None = None
        self._cost_records: SQLiteCostRecordRepository | None = None
        self._messages: SQLiteMessageRepository | None = None
        self._lifecycle_events: SQLiteLifecycleEventRepository | None = None
        self._task_metrics: SQLiteTaskMetricRepository | None = None
        self._collaboration_metrics: SQLiteCollaborationMetricRepository | None = None
        self._parked_contexts: SQLiteParkedContextRepository | None = None
        self._audit_entries: SQLiteAuditRepository | None = None
        self._users: SQLiteUserRepository | None = None
        self._api_keys: SQLiteApiKeyRepository | None = None
        self._checkpoints: SQLiteCheckpointRepository | None = None
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
        self._project_cost_aggregates: SQLiteProjectCostAggregateRepository | None = (
            None
        )
        self._fine_tune_checkpoints: FineTuneCheckpointRepository | None = None
        self._fine_tune_runs: FineTuneRunRepository | None = None
        self._training_plans: SQLiteTrainingPlanRepository | None = None
        self._training_results: SQLiteTrainingResultRepository | None = None
        self._custom_rules: SQLiteCustomRuleRepository | None = None
        self._sessions: SQLiteSessionRepository | None = None
        self._refresh_tokens: SQLiteRefreshTokenRepository | None = None
        self._idempotency_keys: SQLiteIdempotencyRepository | None = None
        self._mcp_installations: SQLiteMcpInstallationRepository | None = None
        self._org_facts: SQLiteOrgFactRepository | None = None
        self._ontology_entities: SQLiteOntologyEntityRepository | None = None
        self._ontology_drift: SQLiteOntologyDriftReportRepository | None = None
        # Cached lockout repository -- in-memory cache must survive
        # across ``build_lockouts`` calls, otherwise ``is_locked`` is
        # always False on a freshly-built instance.
        self._lockouts: SQLiteLockoutRepository | None = None
        self._connections_stub = InMemoryConnectionRepository()
        self._connection_secrets_stub = InMemoryConnectionSecretRepository()
        self._oauth_states_stub = InMemoryOAuthStateRepository()
        self._webhook_receipts_stub = InMemoryWebhookReceiptRepository()

    def _clear_state(self) -> None:
        """Reset connection and repository references to ``None``."""
        self._db = None
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
        self._fine_tune_checkpoints = None
        self._fine_tune_runs = None
        self._training_plans = None
        self._training_results = None
        self._custom_rules = None
        self._sessions = None
        self._refresh_tokens = None
        self._mcp_installations = None
        self._org_facts = None
        self._ontology_entities = None
        self._ontology_drift = None
        self._lockouts = None

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

    def _create_repositories(self) -> None:
        """Instantiate all repository objects from the active connection."""
        assert self._db is not None  # noqa: S101
        self._artifacts = SQLiteArtifactRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._projects = SQLiteProjectRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._tasks = SQLiteTaskRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._cost_records = SQLiteCostRecordRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._messages = SQLiteMessageRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._lifecycle_events = SQLiteLifecycleEventRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._task_metrics = SQLiteTaskMetricRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._collaboration_metrics = SQLiteCollaborationMetricRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._parked_contexts = SQLiteParkedContextRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._audit_entries = SQLiteAuditRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._users = SQLiteUserRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._api_keys = SQLiteApiKeyRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._checkpoints = SQLiteCheckpointRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._heartbeats = SQLiteHeartbeatRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._agent_states = SQLiteAgentStateRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._settings = SQLiteSettingsRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._custom_presets = SQLitePersonalityPresetRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._workflow_definitions = SQLiteWorkflowDefinitionRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._workflow_executions = SQLiteWorkflowExecutionRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._subworkflows = SQLiteSubworkflowRepository(
            self._db,
            write_lock=self._shared_write_lock,
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
                write_lock=self._shared_write_lock,
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
            self._db, write_lock=self._shared_write_lock
        )
        self._risk_overrides = SQLiteRiskOverrideRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._ssrf_violations = SQLiteSsrfViolationRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._circuit_breaker_state = SQLiteCircuitBreakerStateRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._project_cost_aggregates = SQLiteProjectCostAggregateRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._fine_tune_checkpoints = SQLiteFineTuneCheckpointRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._fine_tune_runs = SQLiteFineTuneRunRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._training_plans = SQLiteTrainingPlanRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._training_results = SQLiteTrainingResultRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._custom_rules = SQLiteCustomRuleRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._sessions = SQLiteSessionRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._refresh_tokens = SQLiteRefreshTokenRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._idempotency_keys = SQLiteIdempotencyRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._mcp_installations = SQLiteMcpInstallationRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._org_facts = SQLiteOrgFactRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._ontology_entities = SQLiteOntologyEntityRepository(
            self._db,
            write_lock=self._shared_write_lock,
        )
        self._ontology_drift = SQLiteOntologyDriftReportRepository(
            self._db,
            write_lock=self._shared_write_lock,
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
        """Apply pending schema migrations via Atlas CLI.

        Raises:
            PersistenceConnectionError: If not connected.
            MigrationError: If migration application fails.
        """
        async with self._lifecycle_lock:
            if self._db is None:
                msg = "Cannot migrate: not connected"
                logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
                raise PersistenceConnectionError(msg)
            db_url = atlas.to_sqlite_url(self._config.path)
            await atlas.migrate_apply(db_url)

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        return self._db is not None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("sqlite")

    def _require_connected[T](self, repo: T | None, name: str) -> T:
        """Return *repo* or raise if the backend is not connected.

        Args:
            repo: Repository instance (``None`` when disconnected).
            name: Repository name for the error message.

        Raises:
            PersistenceConnectionError: If *repo* is ``None``.
        """
        if repo is None:
            msg = f"Not connected -- call connect() before accessing {name}"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return repo

    @property
    def tasks(self) -> SQLiteTaskRepository:
        """Repository for Task persistence."""
        return self._require_connected(self._tasks, "tasks")

    @property
    def cost_records(self) -> SQLiteCostRecordRepository:
        """Repository for CostRecord persistence."""
        return self._require_connected(self._cost_records, "cost_records")

    @property
    def messages(self) -> SQLiteMessageRepository:
        """Repository for Message persistence."""
        return self._require_connected(self._messages, "messages")

    @property
    def lifecycle_events(self) -> SQLiteLifecycleEventRepository:
        """Repository for AgentLifecycleEvent persistence."""
        return self._require_connected(self._lifecycle_events, "lifecycle_events")

    @property
    def task_metrics(self) -> SQLiteTaskMetricRepository:
        """Repository for TaskMetricRecord persistence."""
        return self._require_connected(self._task_metrics, "task_metrics")

    @property
    def collaboration_metrics(self) -> SQLiteCollaborationMetricRepository:
        """Repository for CollaborationMetricRecord persistence."""
        return self._require_connected(
            self._collaboration_metrics, "collaboration_metrics"
        )

    @property
    def parked_contexts(self) -> SQLiteParkedContextRepository:
        """Repository for ParkedContext persistence."""
        return self._require_connected(self._parked_contexts, "parked_contexts")

    @property
    def audit_entries(self) -> SQLiteAuditRepository:
        """Repository for AuditEntry persistence."""
        return self._require_connected(self._audit_entries, "audit_entries")

    @property
    def decision_records(self) -> SQLiteDecisionRepository:
        """Repository for DecisionRecord persistence (decisions drop-box)."""
        return self._require_connected(self._decision_records, "decision_records")

    @property
    def users(self) -> SQLiteUserRepository:
        """Repository for User persistence."""
        return self._require_connected(self._users, "users")

    @property
    def api_keys(self) -> SQLiteApiKeyRepository:
        """Repository for ApiKey persistence."""
        return self._require_connected(self._api_keys, "api_keys")

    @property
    def checkpoints(self) -> SQLiteCheckpointRepository:
        """Repository for Checkpoint persistence."""
        return self._require_connected(self._checkpoints, "checkpoints")

    @property
    def heartbeats(self) -> SQLiteHeartbeatRepository:
        """Repository for Heartbeat persistence."""
        return self._require_connected(self._heartbeats, "heartbeats")

    @property
    def agent_states(self) -> SQLiteAgentStateRepository:
        """Repository for AgentRuntimeState persistence."""
        return self._require_connected(self._agent_states, "agent_states")

    @property
    def settings(self) -> SQLiteSettingsRepository:
        """Repository for namespaced settings persistence."""
        return self._require_connected(self._settings, "settings")

    @property
    def artifacts(self) -> SQLiteArtifactRepository:
        """Repository for Artifact persistence."""
        return self._require_connected(self._artifacts, "artifacts")

    @property
    def projects(self) -> SQLiteProjectRepository:
        """Repository for Project persistence."""
        return self._require_connected(self._projects, "projects")

    @property
    def project_cost_aggregates(
        self,
    ) -> SQLiteProjectCostAggregateRepository:
        """Repository for durable project cost aggregates."""
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
        """Repository for fine-tune pipeline run persistence."""
        return self._require_connected(
            self._fine_tune_runs,
            "fine_tune_runs",
        )

    @property
    def custom_presets(self) -> SQLitePersonalityPresetRepository:
        """Repository for custom personality preset persistence."""
        return self._require_connected(self._custom_presets, "custom_presets")

    @property
    def workflow_definitions(self) -> SQLiteWorkflowDefinitionRepository:
        """Repository for workflow definition persistence."""
        return self._require_connected(
            self._workflow_definitions,
            "workflow_definitions",
        )

    @property
    def workflow_executions(self) -> SQLiteWorkflowExecutionRepository:
        """Repository for workflow execution persistence."""
        return self._require_connected(
            self._workflow_executions,
            "workflow_executions",
        )

    @property
    def subworkflows(self) -> SQLiteSubworkflowRepository:
        """Repository for versioned subworkflow persistence."""
        return self._require_connected(
            self._subworkflows,
            "subworkflows",
        )

    @property
    def workflow_versions(self) -> VersionRepository[WorkflowDefinition]:
        """Repository for workflow definition version persistence."""
        return self._require_connected(
            self._workflow_versions,
            "workflow_versions",
        )

    @property
    def identity_versions(self) -> VersionRepository[AgentIdentity]:
        """Repository for AgentIdentity version snapshot persistence."""
        return self._require_connected(
            self._identity_versions,
            "identity_versions",
        )

    @property
    def evaluation_config_versions(
        self,
    ) -> VersionRepository[EvaluationConfig]:
        """Repository for EvaluationConfig version snapshot persistence."""
        return self._require_connected(
            self._evaluation_config_versions,
            "evaluation_config_versions",
        )

    @property
    def budget_config_versions(
        self,
    ) -> VersionRepository[BudgetConfig]:
        """Repository for BudgetConfig version snapshot persistence."""
        return self._require_connected(
            self._budget_config_versions,
            "budget_config_versions",
        )

    @property
    def company_versions(
        self,
    ) -> VersionRepository[Company]:
        """Repository for Company version snapshot persistence."""
        return self._require_connected(
            self._company_versions,
            "company_versions",
        )

    @property
    def role_versions(
        self,
    ) -> VersionRepository[Role]:
        """Repository for Role version snapshot persistence."""
        return self._require_connected(
            self._role_versions,
            "role_versions",
        )

    @property
    def risk_overrides(self) -> SQLiteRiskOverrideRepository:
        """Repository for risk tier override persistence."""
        return self._require_connected(
            self._risk_overrides,
            "risk_overrides",
        )

    @property
    def ssrf_violations(self) -> SQLiteSsrfViolationRepository:
        """Repository for SSRF violation record persistence."""
        return self._require_connected(
            self._ssrf_violations,
            "ssrf_violations",
        )

    @property
    def circuit_breaker_state(self) -> SQLiteCircuitBreakerStateRepository:
        """Repository for circuit breaker state persistence."""
        return self._require_connected(
            self._circuit_breaker_state,
            "circuit_breaker_state",
        )

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
    def training_plans(self) -> SQLiteTrainingPlanRepository:
        """Repository for training plan persistence."""
        return self._require_connected(
            self._training_plans,
            "training_plans",
        )

    @property
    def training_results(self) -> SQLiteTrainingResultRepository:
        """Repository for training result persistence."""
        return self._require_connected(
            self._training_results,
            "training_results",
        )

    @property
    def custom_rules(self) -> SQLiteCustomRuleRepository:
        """Repository for custom signal rule persistence."""
        return self._require_connected(
            self._custom_rules,
            "custom_rules",
        )

    @property
    def sessions(self) -> SQLiteSessionRepository:
        """Repository for hybrid session state (durable + in-memory cache)."""
        return self._require_connected(self._sessions, "sessions")

    @property
    def refresh_tokens(self) -> SQLiteRefreshTokenRepository:
        """Repository for single-use refresh-token rotation."""
        return self._require_connected(
            self._refresh_tokens,
            "refresh_tokens",
        )

    @property
    def idempotency_keys(self) -> SQLiteIdempotencyRepository:
        """Repository for persistent idempotency keys (#1599)."""
        return self._require_connected(
            self._idempotency_keys,
            "idempotency_keys",
        )

    @property
    def mcp_installations(self) -> SQLiteMcpInstallationRepository:
        """Repository for MCP catalog installations."""
        return self._require_connected(
            self._mcp_installations,
            "mcp_installations",
        )

    @property
    def org_facts(self) -> SQLiteOrgFactRepository:
        """Repository for organizational fact persistence (MVCC)."""
        return self._require_connected(self._org_facts, "org_facts")

    @property
    def ontology_entities(self) -> SQLiteOntologyEntityRepository:
        """Repository for ontology entity definitions."""
        return self._require_connected(
            self._ontology_entities,
            "ontology_entities",
        )

    @property
    def ontology_drift(self) -> SQLiteOntologyDriftReportRepository:
        """Repository for ontology drift reports."""
        return self._require_connected(
            self._ontology_drift,
            "ontology_drift",
        )

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Return the cached lockout repository (built once per connection).

        The lockout repo maintains a process-local in-memory cache
        (``_locked``) on the auth hot path.  Returning a fresh instance
        on every call would reset that cache and silently "unlock"
        every user.  The cache is cleared on ``disconnect`` via
        ``_clear_state``.  The shared write lock is passed through so
        lockout transactions serialize with other repositories writing
        to the same aiosqlite connection.
        """
        if self._lockouts is None:
            self._lockouts = SQLiteLockoutRepository(
                self.get_db(),
                auth_config,
                write_lock=self._shared_write_lock,
            )
        return self._lockouts

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,  # noqa: ARG002
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository.

        ``notify_channel`` is ignored by SQLite (no cross-instance
        NOTIFY/LISTEN).
        """
        from synthorg.persistence.sqlite.escalation_repo import (  # noqa: PLC0415
            SQLiteEscalationRepository,
        )

        db = self.get_db()
        return SQLiteEscalationRepository(db)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend."""
        from synthorg.ontology.versioning import (  # noqa: PLC0415
            create_ontology_versioning,
        )

        return create_ontology_versioning(self.get_db())

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.
        """
        result = await self.settings.get(NotBlankStr("_system"), key)
        return result[0] if result is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.
        """
        updated_at = datetime.now(UTC).isoformat()
        await self.settings.set(
            NotBlankStr("_system"),
            key,
            value,
            updated_at,
        )
