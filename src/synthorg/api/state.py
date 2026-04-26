"""Application state container.

Holds typed references to core services, injected into
``app.state`` at startup and accessed by controllers via
``request.app.state``.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.api.auth.presence import UserPresence
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auth.ticket_store import WsTicketStore
from synthorg.api.cursor import CursorSecret  # noqa: TC001
from synthorg.api.errors import ServiceUnavailableError
from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.api.services.idempotency_service import IdempotencyService
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.state_services import AppStateServicesMixin
from synthorg.approval.protocol import ApprovalStoreProtocol  # noqa: TC001
from synthorg.backup.service import BackupService  # noqa: TC001
from synthorg.budget.coordination_store import (
    CoordinationMetricsStore,  # noqa: TC001
)
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.client.simulation_state import ClientSimulationState  # noqa: TC001
from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
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
from synthorg.communication.event_stream.interrupt import (
    InterruptStore,  # noqa: TC001
)
from synthorg.communication.event_stream.stream import EventStreamHub  # noqa: TC001
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,  # noqa: TC001
)
from synthorg.communication.meeting.scheduler import MeetingScheduler  # noqa: TC001
from synthorg.config.schema import RootConfig  # noqa: TC001
from synthorg.engine.approval_gate import ApprovalGate  # noqa: TC001
from synthorg.engine.coordination.service import MultiAgentCoordinator  # noqa: TC001
from synthorg.engine.review_gate import ReviewGateService  # noqa: TC001
from synthorg.engine.task_engine import TaskEngine  # noqa: TC001
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler  # noqa: TC001
from synthorg.hr.performance.tracker import PerformanceTracker  # noqa: TC001
from synthorg.hr.registry import AgentRegistryService  # noqa: TC001
from synthorg.hr.scaling.service import ScalingService  # noqa: TC001
from synthorg.hr.training.service import TrainingService  # noqa: TC001
from synthorg.memory.embedding.fine_tune_orchestrator import (
    FineTuneOrchestrator,  # noqa: TC001
)
from synthorg.notifications.dispatcher import (
    NotificationDispatcher,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP, API_SERVICE_UNAVAILABLE
from synthorg.observability.prometheus_collector import (
    PrometheusCollector,  # noqa: TC001
)
from synthorg.observability.tracing.protocol import TraceHandler  # noqa: TC001
from synthorg.ontology.drift.service import DriftDetectionService  # noqa: TC001
from synthorg.ontology.drift.store import DriftReportStore  # noqa: TC001
from synthorg.ontology.service import OntologyService  # noqa: TC001
from synthorg.ontology.sync import OntologyOrgMemorySync  # noqa: TC001
from synthorg.persistence.artifact_storage import (
    ArtifactStorageBackend,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    LockoutRepository as LockoutStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository as RefreshStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    SessionRepository as SessionStore,  # noqa: TC001
)
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001
from synthorg.providers.health import ProviderHealthTracker  # noqa: TC001
from synthorg.providers.management.service import (
    ProviderManagementService,
)
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.providers.routing.router import ModelRouter  # noqa: TC001
from synthorg.security.audit import AuditLog  # noqa: TC001
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler  # noqa: TC001
from synthorg.security.trust.service import TrustService  # noqa: TC001
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService  # noqa: TC001
from synthorg.telemetry.collector import TelemetryCollector  # noqa: TC001
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

    # Imported under TYPE_CHECKING so the optional ``synthorg[distributed]``
    # extra is not required at runtime for deployments that do not use the
    # distributed task queue.
    from synthorg.workers.claim import JetStreamTaskQueue

logger = get_logger(__name__)


class AppState(AppStateServicesMixin):
    """Typed application state container.

    Service fields accept ``None`` for dev/test mode. Property
    accessors raise ``ServiceUnavailableError`` (503) when missing.
    """

    __slots__ = (
        "_a2a_card_builder",
        "_a2a_client",
        "_a2a_peer_registry",
        "_activity_feed_service",
        "_agent_health_service",
        "_agent_registry",
        "_agent_version_service",
        "_analytics_service",
        "_approval_gate",
        "_approval_timeout_scheduler",
        "_artifact_facade_service",
        "_artifact_storage",
        "_audit_log",
        "_audit_read_service",
        "_auth_service",
        "_backup_facade_service",
        "_backup_service",
        "_bridge_config_applied",
        "_ceremony_policy_service",
        "_ceremony_scheduler",
        "_client_facade_service",
        "_client_simulation_state",
        "_company_read_service",
        "_config_resolver",
        "_connection_catalog",
        "_connection_service",
        "_coordination_metrics_store",
        "_coordination_service",
        "_coordinator",
        "_cost_tracker",
        "_cursor_secret",
        "_delegation_record_store",
        "_department_service",
        "_distributed_task_queue",
        "_drift_detection_service",
        "_drift_report_store",
        "_escalation_notify_subscriber",
        "_escalation_processor",
        "_escalation_registry",
        "_escalation_store",
        "_escalation_sweeper",
        "_evaluation_version_service",
        "_event_stream_hub",
        "_events_read_service",
        "_fine_tune_orchestrator",
        "_health_prober_service",
        "_idempotency_service",
        "_integration_health_facade_service",
        "_interrupt_store",
        "_lockout_store",
        "_mcp_catalog_facade_service",
        "_mcp_catalog_service",
        "_mcp_installations_repo",
        "_meeting_orchestrator",
        "_meeting_scheduler",
        "_meeting_service",
        "_memory_service",
        "_message_bus",
        "_message_service",
        "_model_router",
        "_notification_dispatcher",
        "_oauth_facade_service",
        "_oauth_token_manager",
        "_ontology_facade_service",
        "_ontology_service",
        "_ontology_sync_service",
        "_org_mutation_service",
        "_per_op_concurrency_config",
        "_per_op_rate_limit_config",
        "_performance_tracker",
        "_persistence",
        "_personality_service",
        "_project_facade_service",
        "_prometheus_collector",
        "_provider_health_tracker",
        "_provider_management",
        "_provider_read_service",
        "_provider_registry",
        "_quality_facade_service",
        "_refresh_store",
        "_reports_service",
        "_requests_facade_service",
        "_review_facade_service",
        "_review_gate_service",
        "_role_version_service",
        "_scaling_decision_service",
        "_scaling_service",
        "_self_improvement_service",
        "_session_store",
        "_settings_read_service",
        "_settings_service",
        "_setup_facade_service",
        "_shutdown_requested",
        "_signals_service",
        "_simulation_facade_service",
        "_subworkflow_service",
        "_task_engine",
        "_team_service",
        "_telemetry_collector",
        "_template_pack_facade_service",
        "_ticket_store",
        "_tool_invocation_tracker",
        "_trace_handler",
        "_training_service",
        "_trust_service",
        "_tunnel_provider",
        "_tunnel_service",
        "_user_facade_service",
        "_user_presence",
        "_webhook_event_bridge",
        "_webhook_replay_protector",
        "_webhook_service",
        "_workflow_execution_service",
        "_workflow_service",
        "_workflow_version_service",
        "_ws_auth_timeout_seconds",
        "approval_store",
        "config",
        "startup_time",
    )

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        *,
        config: RootConfig,
        approval_store: ApprovalStoreProtocol,
        persistence: PersistenceBackend | None = None,
        message_bus: MessageBus | None = None,
        cost_tracker: CostTracker | None = None,
        auth_service: AuthService | None = None,
        task_engine: TaskEngine | None = None,
        approval_gate: ApprovalGate | None = None,
        coordinator: MultiAgentCoordinator | None = None,
        agent_registry: AgentRegistryService | None = None,
        performance_tracker: PerformanceTracker | None = None,
        meeting_orchestrator: MeetingOrchestrator | None = None,
        meeting_scheduler: MeetingScheduler | None = None,
        ceremony_scheduler: CeremonyScheduler | None = None,
        settings_service: SettingsService | None = None,
        provider_registry: ProviderRegistry | None = None,
        model_router: ModelRouter | None = None,
        provider_health_tracker: ProviderHealthTracker | None = None,
        tool_invocation_tracker: ToolInvocationTracker | None = None,
        delegation_record_store: DelegationRecordStore | None = None,
        event_stream_hub: EventStreamHub | None = None,
        interrupt_store: InterruptStore | None = None,
        artifact_storage: ArtifactStorageBackend | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
        ontology_service: OntologyService | None = None,
        audit_log: AuditLog | None = None,
        trust_service: TrustService | None = None,
        coordination_metrics_store: CoordinationMetricsStore | None = None,
        connection_catalog: ConnectionCatalog | None = None,
        oauth_token_manager: OAuthTokenManager | None = None,
        health_prober_service: HealthProberService | None = None,
        tunnel_provider: TunnelProvider | None = None,
        webhook_event_bridge: WebhookEventBridge | None = None,
        mcp_catalog_service: CatalogService | None = None,
        mcp_installations_repo: McpInstallationRepository | None = None,
        training_service: TrainingService | None = None,
        startup_time: float = 0.0,
    ) -> None:
        self.config = config
        self.approval_store = approval_store
        self._escalation_store: EscalationQueueStore | None = None
        self._escalation_registry: PendingFuturesRegistry | None = None
        self._escalation_processor: DecisionProcessor | None = None
        self._escalation_sweeper: EscalationExpirationSweeper | None = None
        self._escalation_notify_subscriber: EscalationNotifySubscriber | None = None
        self._approval_gate = approval_gate
        self._artifact_storage = artifact_storage
        self._audit_log = audit_log
        self._backup_service: BackupService | None = None
        self._coordination_metrics_store = coordination_metrics_store
        self._notification_dispatcher = notification_dispatcher
        self._ontology_service = ontology_service
        self._drift_report_store: DriftReportStore | None = None
        self._drift_detection_service: DriftDetectionService | None = None
        self._ontology_sync_service: OntologyOrgMemorySync | None = None
        self._persistence = persistence
        self._message_bus = message_bus
        self._cost_tracker = cost_tracker
        self._auth_service = auth_service
        self._task_engine = task_engine
        self._distributed_task_queue: JetStreamTaskQueue | None = None
        self._coordinator = coordinator
        self._agent_registry = agent_registry
        self._performance_tracker = performance_tracker
        self._trust_service = trust_service
        self._telemetry_collector: TelemetryCollector | None = None
        self._meeting_orchestrator = meeting_orchestrator
        self._meeting_scheduler = meeting_scheduler
        self._ceremony_scheduler = ceremony_scheduler
        self._settings_service = settings_service
        self._provider_registry = provider_registry
        self._model_router = model_router
        self._provider_health_tracker = provider_health_tracker
        self._tool_invocation_tracker = tool_invocation_tracker
        self._training_service = training_service
        self._delegation_record_store = delegation_record_store
        self._event_stream_hub = event_stream_hub
        self._interrupt_store = interrupt_store
        self._connection_catalog = connection_catalog
        self._oauth_token_manager = oauth_token_manager
        self._health_prober_service = health_prober_service
        self._tunnel_provider = tunnel_provider
        self._webhook_event_bridge = webhook_event_bridge
        self._webhook_replay_protector: object | None = None
        # Lazily constructed when first accessed via the property; the
        # service wraps ``persistence.idempotency_keys`` and lives only
        # if a persistence backend is configured.
        self._idempotency_service: IdempotencyService | None = None
        self._a2a_card_builder: AgentCardBuilder | None = None
        self._a2a_client: A2AClient | None = None
        self._a2a_peer_registry: PeerRegistry | None = None
        self._mcp_catalog_service = mcp_catalog_service
        self._mcp_installations_repo = mcp_installations_repo
        self._prometheus_collector: PrometheusCollector | None = None
        self._trace_handler: TraceHandler | None = None
        self._fine_tune_orchestrator: FineTuneOrchestrator | None = None
        self._config_resolver: ConfigResolver | None = None
        # One-shot flag: on_startup applies bridge-config settings
        # exactly once per ``AppState`` lifetime, even when the
        # Litestar lifespan re-enters (shared-app test fixtures or
        # multiple lifespan cycles). Preserves the httpx/SMTP clients
        # built into the notification-dispatcher sinks rather than
        # rebuilding and closing them on every startup.
        self._bridge_config_applied: bool = False
        self._provider_management: ProviderManagementService | None = None
        self._org_mutation_service: OrgMutationService | None = None
        # Shutdown flag observable by long-lived subsystems.
        # ``install_shutdown_handlers`` sets it when SIGTERM/SIGINT
        # arrive so reconcile loops can exit early instead of waiting
        # for lifespan cancellation.  Constructed eagerly so that two
        # concurrent first-reads (e.g. handler + reconcile task) cannot
        # each allocate their own ``Event`` and leave one observer
        # stranded on a stale reference.  Constructing an
        # ``asyncio.Event`` outside a running loop is safe -- it only
        # acquires a loop reference on ``.wait()`` / ``.set()``.
        self._shutdown_requested: asyncio.Event = asyncio.Event()
        # Opaque pagination cursor HMAC secret.  Set by ``create_app`` from
        # ``api.pagination.cursor_secret`` (settings) or
        # ``SYNTHORG_PAGINATION_CURSOR_SECRET`` (env); falls back to an
        # ephemeral per-process key with a WARNING log so tokens become
        # invalid across restarts in deployments that never configured one.
        self._cursor_secret: CursorSecret | None = None
        # Per-operation rate-limit + concurrency configs live on
        # AppState so the settings subscriber can hot-swap them when
        # operators edit ``api.per_op_rate_limit_*`` or
        # ``api.per_op_concurrency_*``.  Guards and the inflight
        # middleware read them via the properties below.  Stores stay
        # on the Litestar State dict (built once, never swapped).
        self._per_op_rate_limit_config: PerOpRateLimitConfig | None = None
        self._per_op_concurrency_config: PerOpConcurrencyConfig | None = None
        # WebSocket auth-handshake timeout in seconds.  Baked in at
        # startup by ``_apply_bridge_config`` from
        # ``api.ws_auth_timeout_seconds`` (restart_required) so the
        # ``/ws`` handler does not reach back through the resolver on
        # every connection.  Retains the built-in default when the
        # bridge cannot be resolved.
        self._ws_auth_timeout_seconds: float = 10.0
        self._init_derived_services(
            settings_service=settings_service,
            config=config,
            persistence=persistence,
        )
        self._review_gate_service: ReviewGateService | None = None
        self._scaling_service: ScalingService | None = None
        self._init_facade_service_slots()
        self._client_simulation_state: ClientSimulationState | None = None
        self._approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None
        self._session_store: SessionStore | None = None
        self._lockout_store: LockoutStore | None = None
        self._refresh_store: RefreshStore | None = None
        self._ticket_store = WsTicketStore()
        self._user_presence = UserPresence()
        self.startup_time = startup_time

    def _init_derived_services(
        self,
        *,
        settings_service: SettingsService | None,
        config: RootConfig,
        persistence: PersistenceBackend | None,
    ) -> None:
        """Build services that depend on other injected services.

        Constructs into locals first, then assigns atomically so a
        failure in any constructor leaves AppState unchanged.
        """
        if settings_service is None:
            return
        resolver = ConfigResolver(
            settings_service=settings_service,
            config=config,
        )
        management = ProviderManagementService(
            settings_service=settings_service,
            config_resolver=resolver,
            app_state=self,
            config=config,
        )
        org_mutations = OrgMutationService(
            settings_service=settings_service,
            config_resolver=resolver,
            budget_config_versions=(
                persistence.budget_config_versions if persistence is not None else None
            ),
            company_versions=(
                persistence.company_versions if persistence is not None else None
            ),
        )
        self._config_resolver = resolver
        self._provider_management = management
        self._org_mutation_service = org_mutations

    def _set_once(self, attr: str, value: object, label: str) -> None:
        """Set a private attribute once; raise if already configured."""
        if getattr(self, attr) is not None:
            logger.error(
                API_APP_STARTUP,
                action="service_already_configured",
                service=label,
            )
            msg = f"{label} already configured"
            raise RuntimeError(msg)
        setattr(self, attr, value)
        logger.info(
            API_APP_STARTUP,
            action="service_configured",
            service=label,
        )

    def _require_service[T](self, service: T | None, name: str) -> T:
        """Return *service* or raise 503 if not configured."""
        if service is None:
            logger.warning(API_SERVICE_UNAVAILABLE, service=name)
            msg = f"{name.replace('_', ' ').title()} not configured"
            raise ServiceUnavailableError(msg)
        return service

    @property
    def has_persistence(self) -> bool:
        """Check whether the persistence backend is configured."""
        return self._persistence is not None

    @property
    def persistence(self) -> PersistenceBackend:
        """Return persistence backend or raise 503."""
        return self._require_service(self._persistence, "persistence")  # type: ignore[no-any-return]

    @property
    def idempotency_service(self) -> IdempotencyService:
        """Return the idempotency service, lazily wrapping ``idempotency_keys``.

        Raises ``ServiceUnavailableError`` (503) when persistence is
        not configured -- the service has no in-memory fallback because
        idempotency must survive restart by definition.
        """
        if self._idempotency_service is not None:
            return self._idempotency_service
        backend = self._require_service(self._persistence, "persistence")
        service = IdempotencyService(backend.idempotency_keys)
        self._idempotency_service = service
        return service

    @property
    def shutdown_requested(self) -> asyncio.Event:
        """Return the shutdown ``asyncio.Event``.

        SIGTERM/SIGINT handlers set the event; long-lived subsystems
        ``await`` or poll it to exit early.  Constructed eagerly in
        ``__init__`` so every reader sees the same instance.
        """
        return self._shutdown_requested

    @property
    def cursor_secret(self) -> CursorSecret:
        """Return the opaque-pagination cursor HMAC secret.

        Wired once by ``create_app`` from configuration; never ``None``
        after startup.  Tests that bypass ``create_app`` must call
        :meth:`set_cursor_secret` explicitly.
        """
        return self._require_service(self._cursor_secret, "cursor_secret")

    def set_cursor_secret(self, secret: CursorSecret) -> None:
        """Attach the opaque-pagination cursor HMAC secret (once-only)."""
        self._set_once("_cursor_secret", secret, "cursor_secret")

    @property
    def has_prometheus_collector(self) -> bool:
        """Check whether the Prometheus collector is configured."""
        return self._prometheus_collector is not None

    @property
    def prometheus_collector(self) -> PrometheusCollector:
        """Return Prometheus collector or raise 503."""
        return self._require_service(
            self._prometheus_collector,
            "prometheus_collector",
        )

    def set_prometheus_collector(
        self,
        collector: PrometheusCollector,
    ) -> None:
        """Attach the Prometheus collector (once-only)."""
        self._set_once("_prometheus_collector", collector, "Prometheus collector")

    @property
    def has_trace_handler(self) -> bool:
        """Check whether the distributed trace handler is configured."""
        return self._trace_handler is not None

    @property
    def trace_handler(self) -> TraceHandler:
        """Return the distributed trace handler or raise 503."""
        return self._require_service(self._trace_handler, "trace_handler")

    def set_trace_handler(self, handler: TraceHandler) -> None:
        """Attach the distributed trace handler (once-only).

        Wired from ``on_startup`` via
        :func:`synthorg.observability.tracing.build_trace_handler`.
        When tracing is disabled, a :class:`NoopTraceHandler` is
        installed so callers always see a valid handler.
        """
        self._set_once("_trace_handler", handler, "trace handler")

    @property
    def has_artifact_storage(self) -> bool:
        """Check whether the artifact storage backend is configured."""
        return self._artifact_storage is not None

    @property
    def artifact_storage(self) -> ArtifactStorageBackend:
        """Return artifact storage backend or raise 503."""
        return self._require_service(self._artifact_storage, "artifact_storage")

    @property
    def has_message_bus(self) -> bool:
        """Check whether the message bus is configured."""
        return self._message_bus is not None

    @property
    def message_bus(self) -> MessageBus:
        """Return message bus or raise 503."""
        return self._require_service(self._message_bus, "message_bus")

    @property
    def has_cost_tracker(self) -> bool:
        """Check whether the cost tracker is configured."""
        return self._cost_tracker is not None

    @property
    def cost_tracker(self) -> CostTracker:
        """Return cost tracker or raise 503."""
        return self._require_service(self._cost_tracker, "cost_tracker")

    @property
    def auth_service(self) -> AuthService:
        """Return auth service or raise 503."""
        return self._require_service(self._auth_service, "auth_service")

    @property
    def task_engine(self) -> TaskEngine:
        """Return task engine or raise 503."""
        return self._require_service(self._task_engine, "task_engine")

    @property
    def has_task_engine(self) -> bool:
        """Check whether the task engine is already configured."""
        return self._task_engine is not None

    def set_task_engine(self, engine: TaskEngine) -> None:
        """Attach the task engine (once-only)."""
        self._set_once("_task_engine", engine, "Task engine")

    @property
    def distributed_task_queue(self) -> JetStreamTaskQueue | None:
        """Return the distributed task queue, or ``None`` when not wired."""
        return self._distributed_task_queue

    def set_distributed_task_queue(
        self,
        task_queue: JetStreamTaskQueue | None,
    ) -> None:
        """Attach the distributed task queue so the lifecycle can manage it.

        Only set when ``queue.enabled`` is true and the ``synthorg[distributed]``
        extra is installed. The lifecycle starts and stops the queue
        alongside the other async services so the dispatcher observer
        sees a connected client before any task state changes fire.

        Logs the transition (attach/detach/replace) at INFO so the
        lifecycle state of ``_distributed_task_queue`` is observable in
        structured logs.
        """
        previous = self._distributed_task_queue
        # Identity check first: assigning the same instance is a noop
        # even when both sides are non-None, so callers that re-set
        # the queue during idempotent rewire paths don't see a
        # misleading "replaced" transition in logs.
        if previous is task_queue:
            transition = "noop"
        elif previous is None:
            transition = "attached"
        elif task_queue is None:
            transition = "detached"
        else:
            transition = "replaced"
        self._distributed_task_queue = task_queue
        logger.info(
            API_APP_STARTUP,
            service="distributed_task_queue",
            transition=transition,
        )

    @property
    def meeting_orchestrator(self) -> MeetingOrchestrator:
        """Return meeting orchestrator or raise 503."""
        return self._require_service(
            self._meeting_orchestrator,
            "meeting_orchestrator",
        )

    @property
    def meeting_scheduler(self) -> MeetingScheduler:
        """Return meeting scheduler or raise 503."""
        return self._require_service(
            self._meeting_scheduler,
            "meeting_scheduler",
        )

    @property
    def ceremony_scheduler(self) -> CeremonyScheduler | None:
        """Return ceremony scheduler, or None if not configured."""
        return self._ceremony_scheduler

    @property
    def approval_gate(self) -> ApprovalGate | None:
        """Return approval gate, or None if not configured."""
        return self._approval_gate

    @property
    def event_stream_hub(self) -> EventStreamHub | None:
        """Return event stream hub, or None if not configured."""
        return self._event_stream_hub

    @property
    def interrupt_store(self) -> InterruptStore | None:
        """Return interrupt store, or None if not configured."""
        return self._interrupt_store

    @property
    def review_gate_service(self) -> ReviewGateService | None:
        """Return review gate service, or None if not configured."""
        return self._review_gate_service

    def set_review_gate_service(self, service: ReviewGateService) -> None:
        """Attach the review gate service (once-only)."""
        self._set_once("_review_gate_service", service, "Review gate service")

    @property
    def scaling_service(self) -> ScalingService | None:
        """Return scaling service, or None if not configured."""
        return self._scaling_service

    def set_scaling_service(self, service: ScalingService) -> None:
        """Attach the scaling service (once-only)."""
        self._set_once("_scaling_service", service, "Scaling service")

    @property
    def has_client_simulation_state(self) -> bool:
        """Check whether client simulation state is configured."""
        return self._client_simulation_state is not None

    @property
    def client_simulation_state(self) -> ClientSimulationState:
        """Return client simulation state or raise 503."""
        return self._require_service(
            self._client_simulation_state,
            "client_simulation_state",
        )

    def set_client_simulation_state(
        self,
        state: ClientSimulationState,
    ) -> None:
        """Attach the client simulation runtime state (once-only)."""
        self._set_once(
            "_client_simulation_state",
            state,
            "Client simulation state",
        )

    @property
    def approval_timeout_scheduler(self) -> ApprovalTimeoutScheduler | None:
        """Return approval timeout scheduler, or None if not configured."""
        return self._approval_timeout_scheduler

    def set_approval_timeout_scheduler(
        self,
        scheduler: ApprovalTimeoutScheduler,
    ) -> None:
        """Attach the approval timeout scheduler (once-only)."""
        self._set_once(
            "_approval_timeout_scheduler",
            scheduler,
            "Approval timeout scheduler",
        )

    @property
    def coordinator(self) -> MultiAgentCoordinator:
        """Return coordinator or raise 503."""
        return self._require_service(self._coordinator, "coordinator")

    @property
    def has_coordinator(self) -> bool:
        """Check whether the coordinator is configured."""
        return self._coordinator is not None

    @property
    def performance_tracker(self) -> PerformanceTracker:
        """Return performance tracker or raise 503."""
        return self._require_service(
            self._performance_tracker,
            "performance_tracker",
        )

    @property
    def agent_registry(self) -> AgentRegistryService:
        """Return agent registry or raise 503."""
        return self._require_service(self._agent_registry, "agent_registry")

    @property
    def has_agent_registry(self) -> bool:
        """Check whether the agent registry is configured."""
        return self._agent_registry is not None

    @property
    def audit_log(self) -> AuditLog:
        """Return audit log or raise 503."""
        return self._require_service(self._audit_log, "audit_log")

    @property
    def has_audit_log(self) -> bool:
        """Check whether the audit log is configured."""
        return self._audit_log is not None

    @property
    def trust_service(self) -> TrustService:
        """Return trust service or raise 503."""
        return self._require_service(
            self._trust_service,
            "trust_service",
        )

    @property
    def has_trust_service(self) -> bool:
        """Check whether the trust service is configured."""
        return self._trust_service is not None

    @property
    def has_telemetry_collector(self) -> bool:
        """Check whether the project telemetry collector is configured."""
        return self._telemetry_collector is not None

    @property
    def telemetry_collector(self) -> TelemetryCollector:
        """Return project telemetry collector or raise 503."""
        return self._require_service(
            self._telemetry_collector,
            "telemetry_collector",
        )

    def set_telemetry_collector(self, collector: TelemetryCollector) -> None:
        """Attach the project telemetry collector (once-only)."""
        self._set_once("_telemetry_collector", collector, "telemetry collector")

    @property
    def coordination_metrics_store(self) -> CoordinationMetricsStore:
        """Return coordination metrics store or raise 503."""
        return self._require_service(
            self._coordination_metrics_store,
            "coordination_metrics_store",
        )

    @property
    def has_coordination_metrics_store(self) -> bool:
        """Check whether the coordination metrics store is configured."""
        return self._coordination_metrics_store is not None
