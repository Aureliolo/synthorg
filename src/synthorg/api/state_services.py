"""Extended service accessors for ``AppState``.

Properties and setters for settings, auth, session, providers, ontology,
backup, integrations, escalation, A2A, and MCP services.  Extracted from
``state.py`` to keep that module's size under the project limit.
"""

from typing import TYPE_CHECKING, Any

from synthorg.api.auth.presence import UserPresence  # noqa: TC001
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auth.ticket_store import WsTicketStore  # noqa: TC001
from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.api.services.org_mutations import OrgMutationService  # noqa: TC001
from synthorg.api.state_services_facades import _FacadesMixin
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
    API_BRIDGE_CONFIG_REJECTED,
)
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.ontology.drift.service import DriftDetectionService  # noqa: TC001
from synthorg.ontology.drift.store import DriftReportStore  # noqa: TC001
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
from synthorg.providers.health import ProviderHealthTracker  # noqa: TC001
from synthorg.providers.management.audit_service import (
    ProviderAuditService,  # noqa: TC001
)
from synthorg.providers.management.service import (
    ProviderManagementService,  # noqa: TC001
)
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.providers.routing.router import ModelRouter  # noqa: TC001
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
    from synthorg.integrations.oauth.token_manager import OAuthTokenManager
    from synthorg.integrations.tunnel.protocol import TunnelProvider

logger = get_logger(__name__)


class AppStateServicesMixin(_FacadesMixin):
    """Service accessor mixin for ``AppState``.

    Every property and setter in this mixin relies on private
    ``_*`` attributes allocated in ``AppState.__slots__`` and
    the ``_require_service`` / ``_set_once`` / ``_init_derived_services``
    helpers declared on the concrete class.

    The facade-service accessors (signals / analytics / reports /
    communication / META-MCP-2 phase 5-9) live in
    :class:`~synthorg.api.state_services_facades._FacadesMixin` to keep
    this module under the 800-line size limit.
    """

    _set_once: Any
    _init_derived_services: Any
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
    _provider_health_tracker: ProviderHealthTracker | None
    _tool_invocation_tracker: ToolInvocationTracker | None
    _training_service: TrainingService | None
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
    _drift_report_store: DriftReportStore | None
    _drift_detection_service: DriftDetectionService | None
    _ontology_sync_service: OntologyOrgMemorySync | None
    _model_router: ModelRouter | None
    _backup_service: BackupService | None
    _connection_catalog: ConnectionCatalog | None
    _tunnel_provider: TunnelProvider | None
    _oauth_token_manager: OAuthTokenManager | None
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

    @property
    def settings_service(self) -> SettingsService:
        """Return settings service or raise 503."""
        return self._require_service(self._settings_service, "settings_service")

    @property
    def has_settings_service(self) -> bool:
        """Check whether the settings service is configured."""
        return self._settings_service is not None

    @property
    def fine_tune_orchestrator(self) -> FineTuneOrchestrator:
        """Return fine-tune orchestrator or raise 503."""
        return self._require_service(
            self._fine_tune_orchestrator,
            "fine_tune_orchestrator",
        )

    @property
    def has_fine_tune_orchestrator(self) -> bool:
        """Check whether the fine-tune orchestrator is configured."""
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
        """Check whether the config resolver is configured."""
        return self._config_resolver is not None

    @property
    def config_resolver(self) -> ConfigResolver:
        """Return the cached config resolver or raise 503."""
        return self._require_service(self._config_resolver, "config_resolver")

    @property
    def has_org_mutation_service(self) -> bool:
        """Check whether the org mutation service is configured."""
        return self._org_mutation_service is not None

    @property
    def org_mutation_service(self) -> OrgMutationService:
        """Return the org mutation service or raise 503."""
        return self._require_service(
            self._org_mutation_service,
            "org_mutation_service",
        )

    @property
    def provider_management(self) -> ProviderManagementService:
        """Return provider management service or raise 503."""
        return self._require_service(
            self._provider_management,
            "provider_management",
        )

    @property
    def has_provider_management(self) -> bool:
        """Check whether the provider management service is configured."""
        return self._provider_management is not None

    @property
    def provider_audit_service(self) -> ProviderAuditService:
        """Return the provider mutation audit service or raise 503.

        Returns the service that owns provider audit-log writes and
        keyset-paginated reads.  Raises ``ServiceUnavailableError``
        (HTTP 503) when the persistence backend has not been wired
        (in-memory fallback paths, pre-bootstrap rigs).  Most call
        sites SHOULD prefer ``has_provider_audit_service`` first.
        """
        return self._require_service(
            self._provider_audit_service,
            "provider_audit_service",
        )

    @property
    def has_provider_audit_service(self) -> bool:
        """Check whether the provider audit service is wired."""
        return self._provider_audit_service is not None

    @property
    def provider_health_tracker(self) -> ProviderHealthTracker:
        """Return provider health tracker or raise 503."""
        return self._require_service(
            self._provider_health_tracker,
            "provider_health_tracker",
        )

    @property
    def has_provider_health_tracker(self) -> bool:
        """Check whether the provider health tracker is configured."""
        return self._provider_health_tracker is not None

    @property
    def has_tool_invocation_tracker(self) -> bool:
        """Check whether the tool invocation tracker is configured."""
        return self._tool_invocation_tracker is not None

    @property
    def tool_invocation_tracker(self) -> ToolInvocationTracker:
        """Return tool invocation tracker or raise 503."""
        return self._require_service(
            self._tool_invocation_tracker,
            "tool_invocation_tracker",
        )

    @property
    def has_training_service(self) -> bool:
        """Check whether the training service is configured."""
        return self._training_service is not None

    @property
    def training_service(self) -> TrainingService:
        """Return training service or raise 503."""
        return self._require_service(
            self._training_service,
            "training_service",
        )

    def set_training_service(self, service: TrainingService) -> None:
        """Attach the training service (once-only)."""
        self._set_once("_training_service", service, "Training service")

    @property
    def has_delegation_record_store(self) -> bool:
        """Check whether the delegation record store is configured."""
        return self._delegation_record_store is not None

    @property
    def delegation_record_store(self) -> DelegationRecordStore:
        """Return delegation record store or raise 503."""
        return self._require_service(
            self._delegation_record_store,
            "delegation_record_store",
        )

    @property
    def has_auth_service(self) -> bool:
        """Check whether the auth service is already configured."""
        return self._auth_service is not None

    @property
    def ticket_store(self) -> WsTicketStore:
        """Return the WebSocket ticket store (always available)."""
        return self._ticket_store

    @property
    def ws_auth_timeout_seconds(self) -> float:
        """Return the WebSocket first-message auth-handshake timeout.

        Populated by ``_apply_bridge_config`` from
        ``api.ws_auth_timeout_seconds`` (``restart_required=True``, so the
        operator-visible contract is "takes effect at the next restart");
        always has a sane built-in default (10.0 s) so the handler
        never reaches back through the resolver per-connection.  The
        setter below is permissive by design -- tests and subsystems that
        need a different value at runtime may call it -- so the effective
        value is whichever ``set_ws_auth_timeout_seconds`` call ran most
        recently.
        """
        return self._ws_auth_timeout_seconds

    def set_ws_auth_timeout_seconds(self, value: float) -> None:
        """Store a validated WebSocket auth timeout on the app state.

        Mirrors the ``set_max_pending_per_user`` pattern used by the
        ticket store: ``_apply_bridge_config`` resolves the setting
        and calls this setter with the validated value at startup,
        which is then read by the ``/ws`` handler.  Repeated calls
        are allowed and the latest value wins -- tests monkeypatch
        this freely and no state in the mixin enforces a single-shot
        contract.  Bounds mirror the
        ``ApiBridgeConfig.ws_auth_timeout_seconds`` Pydantic field;
        the shared ``WS_AUTH_TIMEOUT_{MIN,MAX}_SECONDS`` constants
        keep the two sites aligned (DRY).
        """
        import math  # noqa: PLC0415

        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            WS_AUTH_TIMEOUT_MAX_SECONDS,
            WS_AUTH_TIMEOUT_MIN_SECONDS,
        )

        if not math.isfinite(value):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="non_finite",
                provided_value=repr(value),
            )
            msg = f"ws_auth_timeout_seconds must be finite, got {value!r}"
            raise ValueError(msg)
        if value < WS_AUTH_TIMEOUT_MIN_SECONDS or value > WS_AUTH_TIMEOUT_MAX_SECONDS:
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=WS_AUTH_TIMEOUT_MIN_SECONDS,
                max_value=WS_AUTH_TIMEOUT_MAX_SECONDS,
            )
            msg = (
                "ws_auth_timeout_seconds must be between"
                f" {WS_AUTH_TIMEOUT_MIN_SECONDS} and"
                f" {WS_AUTH_TIMEOUT_MAX_SECONDS} seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_auth_timeout_seconds = value

    @property
    def has_session_store(self) -> bool:
        """Check whether the session store is configured."""
        return self._session_store is not None

    @property
    def session_store(self) -> SessionStore:
        """Return the JWT session store."""
        return self._require_service(
            self._session_store,
            "session_store",
        )

    def set_session_store(self, store: SessionStore) -> None:
        """Attach the session store (once-only)."""
        self._set_once("_session_store", store, "Session store")

    @property
    def has_lockout_store(self) -> bool:
        """Check whether the lockout store is configured."""
        return self._lockout_store is not None

    @property
    def lockout_store(self) -> LockoutStore:
        """Return the account lockout store."""
        return self._require_service(
            self._lockout_store,
            "lockout_store",
        )

    def set_lockout_store(self, store: LockoutStore) -> None:
        """Attach the lockout store (once-only)."""
        self._set_once("_lockout_store", store, "Lockout store")

    @property
    def has_refresh_store(self) -> bool:
        """Check whether the refresh-token store is configured."""
        return self._refresh_store is not None

    @property
    def refresh_store(self) -> RefreshStore:
        """Return the refresh-token store."""
        return self._require_service(
            self._refresh_store,
            "refresh_store",
        )

    def set_refresh_store(self, store: RefreshStore) -> None:
        """Attach the refresh-token store (once-only)."""
        self._set_once("_refresh_store", store, "Refresh store")

    @property
    def user_presence(self) -> UserPresence:
        """Return the user presence tracker (always available)."""
        return self._user_presence

    def set_auth_service(self, service: AuthService) -> None:
        """Attach the auth service (once-only)."""
        self._set_once("_auth_service", service, "Auth service")

    @property
    def has_provider_registry(self) -> bool:
        """Check whether the provider registry is configured."""
        return self._provider_registry is not None

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Return provider registry or raise 503."""
        return self._require_service(
            self._provider_registry,
            "provider_registry",
        )

    def swap_provider_registry(self, registry: ProviderRegistry) -> None:
        """Replace the provider registry (hot-reload)."""
        old_count = (
            len(self._provider_registry) if self._provider_registry is not None else 0
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
        """Check whether the notification dispatcher is configured."""
        return self._notification_dispatcher is not None

    @property
    def notification_dispatcher(self) -> NotificationDispatcher:
        """Return notification dispatcher or raise 503."""
        return self._require_service(
            self._notification_dispatcher, "notification_dispatcher"
        )

    def swap_notification_dispatcher(
        self,
        dispatcher: NotificationDispatcher,
    ) -> NotificationDispatcher | None:
        """Swap the active notification dispatcher and return the prior one."""
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
        """Whether the API startup hook has applied bridge settings."""
        return self._bridge_config_applied

    def mark_bridge_config_applied(self) -> None:
        """Flip :attr:`bridge_config_applied` to ``True`` (one-way)."""
        self._bridge_config_applied = True

    @property
    def ontology_service(self) -> OntologyService:
        """Return ontology service or raise 503."""
        return self._require_service(
            self._ontology_service,
            "ontology_service",
        )

    @property
    def has_ontology_service(self) -> bool:
        """Check whether the ontology service is configured."""
        return self._ontology_service is not None

    @property
    def drift_report_store(self) -> DriftReportStore | None:
        """Return the drift report store, or None if not configured."""
        return self._drift_report_store

    @property
    def drift_detection_service(self) -> DriftDetectionService | None:
        """Return the drift detection service, or None if not configured."""
        return self._drift_detection_service

    @property
    def ontology_sync_service(self) -> OntologyOrgMemorySync | None:
        """Return the ontology sync service, or None if not configured."""
        return self._ontology_sync_service

    def set_drift_report_store(self, store: DriftReportStore) -> None:
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
        """Check whether the model router is configured."""
        return self._model_router is not None

    @property
    def model_router(self) -> ModelRouter:
        """Return model router or raise 503."""
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

    @property
    def has_per_op_rate_limit_config(self) -> bool:
        """Check whether the per-op sliding-window config is set."""
        return self._per_op_rate_limit_config is not None

    @property
    def per_op_rate_limit_config(self) -> PerOpRateLimitConfig:
        """Return the current per-op sliding-window config or raise 503."""
        return self._require_service(
            self._per_op_rate_limit_config,
            "per_op_rate_limit_config",
        )

    def set_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Attach the per-op sliding-window config at startup (once).

        Guards and middleware read through :attr:`per_op_rate_limit_config`
        at request time, so swapping this reference is how the settings
        subscriber applies runtime overrides without restarting the app.
        """
        self._per_op_rate_limit_config = config

    def swap_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Replace the per-op sliding-window config (hot-reload).

        Called by the settings subscriber when operators change
        ``api.per_op_rate_limit_enabled`` or
        ``api.per_op_rate_limit_overrides``.  The store itself is not
        rebuilt -- only the config object swaps, so already-queued
        timestamps remain in place and a ``backend`` flip still needs
        a restart (it is marked ``restart_required=True``).
        """
        old_enabled = (
            self._per_op_rate_limit_config.enabled
            if self._per_op_rate_limit_config is not None
            else None
        )
        self._per_op_rate_limit_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_rate_limit_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def has_per_op_concurrency_config(self) -> bool:
        """Check whether the per-op inflight config is set."""
        return self._per_op_concurrency_config is not None

    @property
    def per_op_concurrency_config(self) -> PerOpConcurrencyConfig:
        """Return the current per-op inflight config or raise 503."""
        return self._require_service(
            self._per_op_concurrency_config,
            "per_op_concurrency_config",
        )

    def set_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Attach the per-op inflight config at startup (once).

        Paired swap target for the inflight subscriber path; mirrors
        :meth:`set_per_op_rate_limit_config` so the two per-op guards
        have symmetric wiring.
        """
        self._per_op_concurrency_config = config

    def swap_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Replace the per-op inflight config (hot-reload).

        Called by the settings subscriber on
        ``api.per_op_concurrency_enabled`` or
        ``api.per_op_concurrency_overrides`` change.  The inflight
        store keeps its counters -- only the enforcement config
        changes.
        """
        old_enabled = (
            self._per_op_concurrency_config.enabled
            if self._per_op_concurrency_config is not None
            else None
        )
        self._per_op_concurrency_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_concurrency_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def has_backup_service(self) -> bool:
        """Check whether the backup service is configured."""
        return self._backup_service is not None

    @property
    def backup_service(self) -> BackupService:
        """Return backup service or raise 503."""
        return self._require_service(self._backup_service, "backup_service")

    def set_backup_service(self, service: BackupService) -> None:
        """Attach the backup service (once-only)."""
        self._set_once("_backup_service", service, "Backup service")

    @property
    def has_connection_catalog(self) -> bool:
        """Check whether the connection catalog is configured."""
        return self._connection_catalog is not None

    @property
    def connection_catalog(self) -> ConnectionCatalog:
        """Return connection catalog or raise 503."""
        return self._require_service(
            self._connection_catalog,
            "connection_catalog",
        )

    @property
    def has_tunnel_provider(self) -> bool:
        """Check whether the tunnel provider is configured."""
        return self._tunnel_provider is not None

    @property
    def tunnel_provider(self) -> TunnelProvider:
        """Return tunnel provider or raise 503."""
        return self._require_service(
            self._tunnel_provider,
            "tunnel_provider",
        )

    @property
    def oauth_token_manager(self) -> OAuthTokenManager | None:
        """Return OAuth token manager, or None if not configured."""
        return self._oauth_token_manager

    @property
    def health_prober_service(self) -> HealthProberService | None:
        """Return health prober service, or None if not configured."""
        return self._health_prober_service

    @property
    def webhook_event_bridge(self) -> WebhookEventBridge | None:
        """Return webhook event bridge, or None if not configured."""
        return self._webhook_event_bridge

    @property
    def escalation_store(self) -> EscalationQueueStore | None:
        """Return the escalation queue store, or None if not configured."""
        return self._escalation_store

    def set_escalation_store(self, store: EscalationQueueStore) -> None:
        """Attach the escalation queue store (once-only)."""
        self._set_once("_escalation_store", store, "escalation store")

    @property
    def escalation_registry(self) -> PendingFuturesRegistry | None:
        """Return the in-process futures registry, or None if not configured."""
        return self._escalation_registry

    def set_escalation_registry(self, registry: PendingFuturesRegistry) -> None:
        """Attach the escalation futures registry (once-only)."""
        self._set_once("_escalation_registry", registry, "escalation registry")

    @property
    def escalation_processor(self) -> DecisionProcessor | None:
        """Return the decision processor strategy, or None if not configured."""
        return self._escalation_processor

    def set_escalation_processor(self, processor: DecisionProcessor) -> None:
        """Attach the escalation decision processor (once-only)."""
        self._set_once("_escalation_processor", processor, "escalation processor")

    @property
    def escalation_sweeper(self) -> EscalationExpirationSweeper | None:
        """Return the background expiration sweeper, or None if not configured."""
        return self._escalation_sweeper

    def set_escalation_sweeper(self, sweeper: EscalationExpirationSweeper) -> None:
        """Attach the escalation expiration sweeper (once-only)."""
        self._set_once("_escalation_sweeper", sweeper, "escalation sweeper")

    @property
    def escalation_notify_subscriber(self) -> EscalationNotifySubscriber | None:
        """Return the cross-instance notify subscriber, or None if not configured."""
        return self._escalation_notify_subscriber

    def set_escalation_notify_subscriber(
        self,
        subscriber: EscalationNotifySubscriber,
    ) -> None:
        """Attach the cross-instance notify subscriber (once-only)."""
        self._set_once(
            "_escalation_notify_subscriber",
            subscriber,
            "escalation notify subscriber",
        )

    @property
    def a2a_card_builder(self) -> AgentCardBuilder:
        """Return the A2A Agent Card builder or raise 503."""
        return self._require_service(
            self._a2a_card_builder,
            "a2a_card_builder",
        )

    def set_a2a_card_builder(self, builder: AgentCardBuilder) -> None:
        """Attach the A2A card builder (once-only)."""
        self._set_once("_a2a_card_builder", builder, "A2A card builder")

    @property
    def a2a_client(self) -> A2AClient:
        """Return the outbound A2A client or raise 503."""
        return self._require_service(
            self._a2a_client,
            "a2a_client",
        )

    def set_a2a_client(self, client: A2AClient) -> None:
        """Attach the outbound A2A client (once-only)."""
        self._set_once("_a2a_client", client, "A2A client")

    @property
    def a2a_peer_registry(self) -> PeerRegistry:
        """Return the A2A peer registry or raise 503."""
        return self._require_service(
            self._a2a_peer_registry,
            "a2a_peer_registry",
        )

    def set_a2a_peer_registry(self, registry: PeerRegistry) -> None:
        """Attach the A2A peer registry (once-only)."""
        self._set_once(
            "_a2a_peer_registry",
            registry,
            "A2A peer registry",
        )

    @property
    def mcp_catalog_service(self) -> CatalogService:
        """Return MCP catalog service or raise 503."""
        return self._require_service(
            self._mcp_catalog_service,
            "mcp_catalog_service",
        )

    def set_mcp_catalog_service(self, service: CatalogService) -> None:
        """Attach the MCP catalog service (once-only)."""
        self._set_once("_mcp_catalog_service", service, "MCP catalog service")

    @property
    def has_mcp_installations_repo(self) -> bool:
        """Check whether the MCP installations repository is configured."""
        return self._mcp_installations_repo is not None

    @property
    def mcp_installations_repo(self) -> McpInstallationRepository:
        """Return the MCP installations repository or raise 503."""
        return self._require_service(
            self._mcp_installations_repo,
            "mcp_installations_repo",
        )

    def set_mcp_installations_repo(
        self,
        repo: McpInstallationRepository,
    ) -> None:
        """Attach the MCP installations repository (once-only)."""
        self._set_once(
            "_mcp_installations_repo",
            repo,
            "MCP installations repository",
        )

    # Facade-service accessors (signals / analytics / reports /
    # communication / META-MCP-2 phases 5-9) live on
    # :class:`~synthorg.api.state_services_facades._FacadesMixin`.

    def set_settings_service(self, settings_service: SettingsService) -> None:
        """Set settings service and rebuild derived services."""
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
