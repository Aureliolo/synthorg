"""Extended service accessors for ``AppState``.

Properties and setters for settings, auth, session, providers, ontology,
backup, integrations, escalation, A2A, and MCP services.  Extracted from
``state.py`` to keep that module's size under the project limit.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    import threading
    from collections import OrderedDict

from synthorg.api.auth.presence import UserPresence  # noqa: TC001
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auth.ticket_store import WsTicketStore  # noqa: TC001
from synthorg.api.services.org_mutations import OrgMutationService  # noqa: TC001
from synthorg.api.services.workflow_rollback_service import (
    WorkflowRollbackService,  # noqa: TC001
)
from synthorg.api.state_services_bridge import _BridgeIntegrationsMixin
from synthorg.api.state_services_facades import _FacadesMixin
from synthorg.api.state_services_locks import _RequestLockAuthMixin
from synthorg.backup.service import BackupService  # noqa: TC001
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,  # noqa: TC001
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,  # noqa: TC001
)
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,  # noqa: TC001
)
from synthorg.hr.training.plan_service import TrainingPlanService  # noqa: TC001
from synthorg.hr.training.service import TrainingService  # noqa: TC001
from synthorg.memory.embedding.fine_tune_orchestrator import (
    FineTuneOrchestrator,  # noqa: TC001
)
from synthorg.notifications.dispatcher import (
    NotificationDispatcher,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
)
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.ontology.drift.service import DriftDetectionService  # noqa: TC001
from synthorg.ontology.service import OntologyService  # noqa: TC001
from synthorg.ontology.sync import OntologyOrgMemorySync  # noqa: TC001
from synthorg.persistence.auth_protocol import (
    LockoutRepository as LockoutStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository as RefreshStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    SessionRepository as SessionStore,  # noqa: TC001
)
from synthorg.persistence.ontology_protocol import (  # noqa: TC001
    OntologyDriftReportRepository,
)
from synthorg.providers.health import ProviderHealthTracker  # noqa: TC001
from synthorg.providers.management.audit_service import (
    ProviderAuditService,  # noqa: TC001
)
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,  # noqa: TC001
)
from synthorg.providers.management.service import (
    ProviderManagementService,  # noqa: TC001
)
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.providers.routing.router import ModelRouter  # noqa: TC001
from synthorg.settings.bridge_configs import (  # noqa: TC001
    ApiBridgeConfig,
    MemoryBridgeConfig,
    WorkersBridgeConfig,
)
from synthorg.settings.resolver import ConfigResolver  # noqa: TC001
from synthorg.settings.service import SettingsService  # noqa: TC001
from synthorg.tools.invocation_tracker import ToolInvocationTracker  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.a2a.agent_card import AgentCardBuilder
    from synthorg.a2a.client import A2AClient
    from synthorg.a2a.peer_registry import PeerRegistry
    from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.health.prober import HealthProberService
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )
    from synthorg.integrations.mcp_catalog.service import CatalogService
    from synthorg.integrations.oauth.state_service import OAuthStateService
    from synthorg.integrations.oauth.token_manager import OAuthTokenManager
    from synthorg.integrations.tunnel.protocol import TunnelProvider
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


class AppStateServicesMixin(
    _FacadesMixin,
    _BridgeIntegrationsMixin,
    _RequestLockAuthMixin,
):
    """Service accessor mixin for ``AppState``.

    Every property and setter in this mixin relies on private
    ``_*`` attributes allocated in ``AppState.__slots__`` and
    the ``_require_service`` / ``_set_once`` / ``_init_derived_services``
    helpers declared on the concrete class.

    The facade-service accessors (signals / analytics / reports /
    communication / infrastructure / organization / integration /
    quality) live in
    :class:`~synthorg.api.state_services_facades._FacadesMixin` to keep
    this module under the 800-line size limit.
    """

    _set_once: Any
    _init_derived_services: Any
    _api_bridge_config: ApiBridgeConfig
    _api_bridge_config_lock: threading.Lock
    _workers_bridge_config: WorkersBridgeConfig
    _workers_bridge_config_lock: threading.Lock
    _memory_bridge_config: MemoryBridgeConfig
    _memory_bridge_config_lock: threading.Lock
    _provider_registry_lock: threading.Lock
    config: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete class)."""
        raise NotImplementedError

    # Slot attrs the mixin reads directly (populated on concrete class).
    _settings_service: SettingsService | None
    _fine_tune_orchestrator: FineTuneOrchestrator | None
    _config_resolver: ConfigResolver | None
    _org_mutation_service: OrgMutationService | None
    _provider_management: ProviderManagementService | None
    _provider_audit_service: ProviderAuditService | None
    _preset_override_service: PresetOverrideService | None
    _provider_health_tracker: ProviderHealthTracker | None
    _tool_invocation_tracker: ToolInvocationTracker | None
    _training_service: TrainingService | None
    _training_plan_service: TrainingPlanService | None
    _workflow_rollback_service: WorkflowRollbackService | None
    _memory_backend: MemoryBackend | None
    _delegation_record_store: DelegationRecordStore | None
    _auth_service: AuthService | None
    _ticket_store: WsTicketStore
    _session_store: SessionStore | None
    _lockout_store: LockoutStore | None
    _refresh_store: RefreshStore | None
    _user_presence: UserPresence
    _provider_registry: ProviderRegistry | None
    _notification_dispatcher: NotificationDispatcher | None
    _bridge_config_applied: bool
    _ontology_service: OntologyService | None
    _drift_report_store: OntologyDriftReportRepository | None
    _drift_detection_service: DriftDetectionService | None
    _ontology_sync_service: OntologyOrgMemorySync | None
    _model_router: ModelRouter | None
    _backup_service: BackupService | None
    _connection_catalog: ConnectionCatalog | None
    _tunnel_provider: TunnelProvider | None
    _oauth_token_manager: OAuthTokenManager | None
    _oauth_state_service: OAuthStateService | None
    _health_prober_service: HealthProberService | None
    _webhook_event_bridge: WebhookEventBridge | None
    _escalation_store: EscalationQueueStore | None
    _escalation_registry: PendingFuturesRegistry | None
    _escalation_processor: DecisionProcessor | None
    _escalation_sweeper: EscalationExpirationSweeper | None
    _escalation_notify_subscriber: EscalationNotifySubscriber | None
    _a2a_card_builder: AgentCardBuilder | None
    _a2a_client: A2AClient | None
    _a2a_peer_registry: PeerRegistry | None
    _mcp_catalog_service: CatalogService | None
    _mcp_installations_repo: McpInstallationRepository | None
    _persistence: Any
    _ws_auth_timeout_seconds: float
    _ws_frame_timeout_seconds: int
    _auth_revalidate_window_seconds: int
    _auth_revalidate_max_failures: int
    _request_locks: OrderedDict[str, asyncio.Lock]
    _request_locks_guard: threading.Lock
    _request_lock_refs: dict[str, int]

    @property
    def settings_service(self) -> SettingsService:
        """Return settings service or raise 503.

        Returns:
            ``SettingsService`` instance.
        """
        return self._require_service(self._settings_service, "settings_service")

    @property
    def has_settings_service(self) -> bool:
        """Check whether the settings service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._settings_service is not None

    @property
    def fine_tune_orchestrator(self) -> FineTuneOrchestrator:
        """Return fine-tune orchestrator or raise 503.

        Returns:
            ``FineTuneOrchestrator`` instance.
        """
        return self._require_service(
            self._fine_tune_orchestrator,
            "fine_tune_orchestrator",
        )

    @property
    def has_fine_tune_orchestrator(self) -> bool:
        """Check whether the fine-tune orchestrator is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._fine_tune_orchestrator is not None

    def set_fine_tune_orchestrator(
        self,
        orchestrator: FineTuneOrchestrator,
    ) -> None:
        """Attach the fine-tune orchestrator (once-only)."""
        self._set_once(
            "_fine_tune_orchestrator",
            orchestrator,
            "Fine-tune orchestrator",
        )

    @property
    def has_config_resolver(self) -> bool:
        """Check whether the config resolver is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._config_resolver is not None

    @property
    def config_resolver(self) -> ConfigResolver:
        """Return the cached config resolver or raise 503.

        Returns:
            ``ConfigResolver`` instance.
        """
        return self._require_service(self._config_resolver, "config_resolver")

    @property
    def has_org_mutation_service(self) -> bool:
        """Check whether the org mutation service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._org_mutation_service is not None

    @property
    def org_mutation_service(self) -> OrgMutationService:
        """Return the org mutation service or raise 503.

        Returns:
            ``OrgMutationService`` instance.
        """
        return self._require_service(
            self._org_mutation_service,
            "org_mutation_service",
        )

    @property
    def provider_management(self) -> ProviderManagementService:
        """Return provider management service or raise 503.

        Returns:
            ``ProviderManagementService`` instance.
        """
        return self._require_service(
            self._provider_management,
            "provider_management",
        )

    @property
    def has_provider_management(self) -> bool:
        """Check whether the provider management service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._provider_management is not None

    @property
    def provider_audit_service(self) -> ProviderAuditService:
        """Return the provider mutation audit service or raise 503.

        Returns the service that owns provider audit-log writes and
        keyset-paginated reads.  Raises ``ServiceUnavailableError``
        (HTTP 503) when the persistence backend has not been wired
        (in-memory fallback paths, pre-bootstrap rigs).  Most call
        sites SHOULD prefer ``has_provider_audit_service`` first.

        Returns:
            ``ProviderAuditService`` instance.
        """
        return self._require_service(
            self._provider_audit_service,
            "provider_audit_service",
        )

    @property
    def has_provider_audit_service(self) -> bool:
        """Check whether the provider audit service is wired.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._provider_audit_service is not None

    @property
    def preset_override_service(self) -> PresetOverrideService:
        """Return the preset override service or raise 503.

        ``None`` when the persistence backend has not been wired
        (in-memory fallback paths).  Callers should prefer
        ``has_preset_override_service`` first.

        Returns:
            ``PresetOverrideService`` instance.
        """
        return self._require_service(
            self._preset_override_service,
            "preset_override_service",
        )

    @property
    def has_preset_override_service(self) -> bool:
        """Check whether the preset override service is wired.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._preset_override_service is not None

    @property
    def provider_health_tracker(self) -> ProviderHealthTracker:
        """Return provider health tracker or raise 503.

        Returns:
            ``ProviderHealthTracker`` instance.
        """
        return self._require_service(
            self._provider_health_tracker,
            "provider_health_tracker",
        )

    @property
    def has_provider_health_tracker(self) -> bool:
        """Check whether the provider health tracker is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._provider_health_tracker is not None

    @property
    def has_tool_invocation_tracker(self) -> bool:
        """Check whether the tool invocation tracker is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._tool_invocation_tracker is not None

    @property
    def tool_invocation_tracker(self) -> ToolInvocationTracker:
        """Return tool invocation tracker or raise 503.

        Returns:
            ``ToolInvocationTracker`` instance.
        """
        return self._require_service(
            self._tool_invocation_tracker,
            "tool_invocation_tracker",
        )

    @property
    def has_training_service(self) -> bool:
        """Check whether the training service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._training_service is not None

    @property
    def training_service(self) -> TrainingService:
        """Return training service or raise 503.

        Returns:
            ``TrainingService`` instance.
        """
        return self._require_service(
            self._training_service,
            "training_service",
        )

    def set_training_service(self, service: TrainingService) -> None:
        """Attach the training service (once-only)."""
        self._set_once("_training_service", service, "Training service")

    @property
    def has_training_plan_service(self) -> bool:
        """Check whether the training plan service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._training_plan_service is not None

    @property
    def training_plan_service(self) -> TrainingPlanService:
        """Return training plan service or raise 503.

        ``TrainingPlanService`` is the audit-aware facade over the
        ``training_plans`` + ``training_results`` repositories.  The
        ``TrainingController`` routes every plan-CRUD write through
        this service so audit logging cannot regress when a new write
        path is added.

        Returns:
            ``TrainingPlanService`` instance.
        """
        return self._require_service(
            self._training_plan_service,
            "training_plan_service",
        )

    def set_training_plan_service(self, service: TrainingPlanService) -> None:
        """Attach the training plan service (once-only)."""
        self._set_once("_training_plan_service", service, "Training plan service")

    @property
    def has_workflow_rollback_service(self) -> bool:
        """Check whether the workflow rollback service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._workflow_rollback_service is not None

    @property
    def workflow_rollback_service(self) -> WorkflowRollbackService:
        """Return workflow rollback service or raise 503.

        ``WorkflowRollbackService`` centralises the live save +
        post-rollback snapshot writes the controller previously made
        directly on the workflow_definitions repository, so audit
        logging cannot regress when a new write path lands in the
        rollback contract.

        Returns:
            ``WorkflowRollbackService`` instance.
        """
        return self._require_service(
            self._workflow_rollback_service,
            "workflow_rollback_service",
        )

    def set_workflow_rollback_service(self, service: WorkflowRollbackService) -> None:
        """Attach the workflow rollback service (once-only)."""
        self._set_once(
            "_workflow_rollback_service", service, "Workflow rollback service"
        )

    @property
    def has_memory_backend(self) -> bool:
        """Check whether a shared MemoryBackend is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._memory_backend is not None

    @property
    def memory_backend(self) -> MemoryBackend:
        """Return the shared memory backend or raise 503.

        Returns:
            ``MemoryBackend`` instance.
        """
        return self._require_service(
            self._memory_backend,
            "memory_backend",
        )

    def set_memory_backend(self, backend: MemoryBackend) -> None:
        """Attach the shared memory backend (once-only)."""
        self._set_once("_memory_backend", backend, "Memory backend")

    @property
    def has_delegation_record_store(self) -> bool:
        """Check whether the delegation record store is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._delegation_record_store is not None

    @property
    def delegation_record_store(self) -> DelegationRecordStore:
        """Return delegation record store or raise 503.

        Returns:
            ``DelegationRecordStore`` instance.
        """
        return self._require_service(
            self._delegation_record_store,
            "delegation_record_store",
        )

    @property
    def has_auth_service(self) -> bool:
        """Check whether the auth service is already configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._auth_service is not None

    @property
    def ticket_store(self) -> WsTicketStore:
        """Return the WebSocket ticket store (always available).

        Returns:
            ``WsTicketStore`` instance.
        """
        return self._ticket_store

    # Request-lock registry + WS/revalidation timeout + session /
    # lockout / refresh / auth-service accessors live on
    # :class:`~synthorg.api.state_services_locks._RequestLockAuthMixin`.

    @property
    def has_provider_registry(self) -> bool:
        """Check whether the provider registry is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._provider_registry is not None

    @property
    def has_active_provider(self) -> bool:
        """Check whether at least one LLM provider is registered.

        The single source of truth for the provider-present switch:
        the task-submission guard and the worker-execution-service
        builder both consult this so "empty company" means exactly the
        same thing in both places.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._provider_registry is not None and len(self._provider_registry) > 0

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Return provider registry or raise 503.

        Returns:
            ``ProviderRegistry`` instance.
        """
        return self._require_service(
            self._provider_registry,
            "provider_registry",
        )

    def swap_provider_registry(self, registry: ProviderRegistry) -> None:
        """Replace the provider registry (hot-reload).

        Serialised under ``_provider_registry_lock`` so the
        read-count-then-replace sequence cannot interleave with a
        concurrent reinit-wake swap (the registry reference itself is
        immutable, so readers always observe a whole old-or-new value).
        """
        with self._provider_registry_lock:
            old_count = (
                len(self._provider_registry)
                if self._provider_registry is not None
                else 0
            )
            self._provider_registry = registry
            logger.info(
                SETTINGS_SERVICE_SWAPPED,
                service="provider_registry",
                old_provider_count=old_count,
                new_provider_count=len(registry),
            )

    @property
    def has_notification_dispatcher(self) -> bool:
        """Check whether the notification dispatcher is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._notification_dispatcher is not None

    @property
    def notification_dispatcher(self) -> NotificationDispatcher:
        """Return notification dispatcher or raise 503.

        Returns:
            ``NotificationDispatcher`` instance.
        """
        return self._require_service(
            self._notification_dispatcher, "notification_dispatcher"
        )

    def swap_notification_dispatcher(
        self,
        dispatcher: NotificationDispatcher,
    ) -> NotificationDispatcher | None:
        """Swap the active notification dispatcher and return the prior one.

        Returns:
            The ``NotificationDispatcher`` value when present, ``None`` otherwise.
        """
        previous = self._notification_dispatcher
        self._notification_dispatcher = dispatcher
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="notification_dispatcher",
            old_id=id(previous) if previous is not None else None,
            new_id=id(dispatcher),
        )
        return previous

    @property
    def bridge_config_applied(self) -> bool:
        """Whether the API startup hook has applied bridge settings.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._bridge_config_applied

    def mark_bridge_config_applied(self) -> None:
        """Flip :attr:`bridge_config_applied` to ``True`` (one-way)."""
        self._bridge_config_applied = True
        logger.info(
            API_APP_STARTUP,
            service="bridge_config",
            transition="applied",
        )

    @property
    def ontology_service(self) -> OntologyService:
        """Return ontology service or raise 503.

        Returns:
            ``OntologyService`` instance.
        """
        return self._require_service(
            self._ontology_service,
            "ontology_service",
        )

    @property
    def has_ontology_service(self) -> bool:
        """Check whether the ontology service is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._ontology_service is not None

    @property
    def drift_report_store(self) -> OntologyDriftReportRepository | None:
        """Return the drift report store, or None if not configured.

        Returns:
            The drift report repository when configured, ``None`` otherwise.
        """
        return self._drift_report_store

    @property
    def drift_detection_service(self) -> DriftDetectionService | None:
        """Return the drift detection service, or None if not configured.

        Returns:
            The ``DriftDetectionService`` value when present, ``None`` otherwise.
        """
        return self._drift_detection_service

    @property
    def ontology_sync_service(self) -> OntologyOrgMemorySync | None:
        """Return the ontology sync service, or None if not configured.

        Returns:
            The ``OntologyOrgMemorySync`` value when present, ``None`` otherwise.
        """
        return self._ontology_sync_service

    def set_ontology_service(self, service: OntologyService) -> None:
        """Attach the ontology service (once-only; auto-wired on startup)."""
        self._set_once("_ontology_service", service, "Ontology service")

    def set_drift_report_store(self, store: OntologyDriftReportRepository) -> None:
        """Attach the drift report store (once-only)."""
        self._set_once("_drift_report_store", store, "Drift report store")

    def set_drift_detection_service(
        self,
        service: DriftDetectionService,
    ) -> None:
        """Attach the drift detection service (once-only)."""
        self._set_once(
            "_drift_detection_service",
            service,
            "Drift detection service",
        )

    def set_ontology_sync_service(
        self,
        service: OntologyOrgMemorySync,
    ) -> None:
        """Attach the ontology sync service (once-only)."""
        self._set_once(
            "_ontology_sync_service",
            service,
            "Ontology sync service",
        )

    @property
    def has_model_router(self) -> bool:
        """Check whether the model router is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._model_router is not None

    @property
    def model_router(self) -> ModelRouter:
        """Return model router or raise 503.

        Returns:
            ``ModelRouter`` instance.
        """
        return self._require_service(self._model_router, "model_router")

    def swap_model_router(self, router: ModelRouter) -> None:
        """Replace the model router (hot-reload)."""
        old_strategy = (
            self._model_router.strategy_name if self._model_router is not None else None
        )
        self._model_router = router
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="model_router",
            old_strategy=old_strategy,
            new_strategy=router.strategy_name,
        )

    # Bridge-config + integration/escalation/A2A/MCP accessors live on
    # :class:`~synthorg.api.state_services_bridge._BridgeIntegrationsMixin`.
    # Facade-service accessors (signals / analytics / reports /
    # communication / META-MCP-2 phases 5-9) live on
    # :class:`~synthorg.api.state_services_facades._FacadesMixin`.

    def set_settings_service(self, settings_service: SettingsService) -> None:
        """Set settings service and rebuild derived services.

        Raises:
            RuntimeError: Raised on the corresponding failure path.
        """
        if self._settings_service is not None:
            logger.error(
                API_APP_STARTUP,
                action="service_already_configured",
                service="settings_service",
            )
            msg = "Settings service already configured"
            raise RuntimeError(msg)
        self._init_derived_services(
            settings_service=settings_service,
            config=self.config,
            persistence=self._persistence,
        )
        self._settings_service = settings_service
        logger.info(
            API_APP_STARTUP,
            action="service_configured",
            service="settings_service",
        )
