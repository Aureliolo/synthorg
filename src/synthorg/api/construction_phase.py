# module-kind: orchestrator
"""Construction-phase service build for the composition root.

``create_app`` delegates the persistence-independent service construction to
:func:`build_construction_services`: it builds ``AppState``, auto-wires the
phase-1 / meeting / integration services, composes every feature's state slice,
runs each feature's ``construction_wirer`` (via ``run_construction_wiring``),
wires the communication-domain services + escalation stack, builds the
bridge / backup / settings-dispatcher / middleware, and returns the handful of
collaborators the composition root threads into route assembly, the lifespan
hooks, and the Litestar build. Keeping it here leaves ``create_app`` a thin
orchestrator (bootstrap -> persistence -> build -> routes -> lifespan -> app).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.api.app_builders import (
    _build_configured_autonomy_change_strategy,
    _build_configured_trust_service,
    _build_performance_tracker,
)
from synthorg.api.app_helpers import _make_expire_callback, _make_meeting_publisher
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auto_wire import auto_wire_meetings, auto_wire_phase1
from synthorg.api.boot_persistence import BootPersistence
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import create_channels_plugin
from synthorg.api.config import ApiConfig
from synthorg.api.construction_wiring import ConstructionDeps, run_construction_wiring
from synthorg.api.cursor import CursorSecret
from synthorg.api.cursor_config import CursorConfig
from synthorg.api.feature_composition import compose_feature_slices
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle_helpers.boot_resolvers import (
    build_default_approval_timeout_scheduler,
    resolve_budget_int,
    resolve_rate_limiter_enabled,
)
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.middleware_factory import _build_middleware
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.backup.factory import build_backup_service
from synthorg.backup.service import BackupService
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.conflict_resolution.escalation import (
    EscalationExpirationSweeper,
    PendingFuturesRegistry,
    build_decision_processor,
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.core.clock import SystemClock
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP, API_SERVICE_AUTO_WIRED
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher

if TYPE_CHECKING:
    from litestar.channels import ChannelsPlugin
    from litestar.types import Middleware

logger = get_logger(__name__)


@dataclass(frozen=True)
class ConstructionResult:
    """The collaborators the composition root needs after the build phase.

    ``app_state`` is fully slice-populated; the remaining fields feed route
    assembly, the lifespan-hook assembly, and the Litestar build.
    """

    app_state: AppState
    message_bus: MessageBus | None
    cost_tracker: CostTracker | None
    task_engine: TaskEngine | None
    provider_registry: ProviderRegistry | None
    meeting_scheduler: MeetingScheduler | None
    connection_catalog: ConnectionCatalog | None
    notification_dispatcher: NotificationDispatcher
    performance_tracker: PerformanceTracker | None
    approval_store: ApprovalStoreProtocol
    bridge: MessageBusBridge | None
    settings_dispatcher: SettingsChangeDispatcher | None
    backup_service: BackupService | None
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None
    plugins: list[ChannelsPlugin]
    middleware: list[Middleware]
    should_auto_wire_settings: bool


def build_construction_services(
    *,
    effective_config: RootConfig,
    api_config: ApiConfig,
    overrides: AppOverrides,
    boot: BootPersistence,
) -> ConstructionResult:
    """Build the persistence-independent services and the populated AppState.

    Args:
        effective_config: The resolved root configuration.
        api_config: The resolved API configuration.
        overrides: Caller-injected dependency doubles (``None`` -> auto-wire).
        boot: The resolved persistence backend, artifact storage, and paths.

    Returns:
        The construction result the composition root threads into route
        assembly, lifespan-hook assembly, and the Litestar build.

    Raises:
        RuntimeError: When the pagination cursor secret is ephemeral.
    """
    persistence = boot.persistence

    # ── Construction-time auto-wire: services that don't need connected
    # persistence ──
    phase1 = auto_wire_phase1(
        effective_config=effective_config,
        persistence=persistence,
        message_bus=overrides.message_bus,
        cost_tracker=overrides.cost_tracker,
        task_engine=overrides.task_engine,
        provider_registry=overrides.provider_registry,
        provider_health_tracker=overrides.provider_health_tracker,
    )
    message_bus = phase1.message_bus
    provider_registry = phase1.provider_registry

    agent_registry = overrides.agent_registry
    if agent_registry is None:
        agent_registry = AgentRegistryService()
        logger.info(API_SERVICE_AUTO_WIRED, service="agent_registry")

    # ── Meeting auto-wire: orchestrator + scheduler (construction-time) ──
    meeting_wire = auto_wire_meetings(
        effective_config=effective_config,
        meeting_orchestrator=overrides.meeting_orchestrator,
        meeting_scheduler=overrides.meeting_scheduler,
        agent_registry=agent_registry,
        provider_registry=provider_registry,
        persistence=persistence,
    )
    meeting_orchestrator = meeting_wire.meeting_orchestrator
    meeting_scheduler = meeting_wire.meeting_scheduler

    channels_plugin = create_channels_plugin()
    expire_callback = _make_expire_callback(channels_plugin)
    approval_store: ApprovalStoreProtocol = (
        overrides.approval_store
        if overrides.approval_store is not None
        else ApprovalStore(on_expire=expire_callback)
    )

    # Wire meeting event publisher to the meetings WS channel.
    if meeting_scheduler is not None and meeting_scheduler._event_publisher is None:  # noqa: SLF001
        meeting_scheduler._event_publisher = _make_meeting_publisher(  # noqa: SLF001
            channels_plugin,
        )

    # Auto-wire performance tracker with composite quality strategy when not
    # explicitly injected (production path).
    performance_tracker = overrides.performance_tracker
    if performance_tracker is None:
        performance_tracker = _build_performance_tracker(
            cost_tracker=phase1.cost_tracker,
            provider_registry=provider_registry,
            perf_config=effective_config.performance,
        )

    notification_dispatcher = build_notification_dispatcher(
        effective_config.notifications,
    )

    integrations = auto_wire_integrations(
        effective_config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
        api_config=api_config,
        ceremony_scheduler=meeting_wire.ceremony_scheduler,
        db_url=boot.db_url,
        resolved_db_path=boot.resolved_db_path,
        boot_db_path=boot.db_path,
    )

    # Auto-wire control-plane services when not injected.
    audit_log = overrides.audit_log or AuditLog()
    coordination_metrics_store = overrides.coordination_metrics_store
    if coordination_metrics_store is None:
        coordination_metrics_store = CoordinationMetricsStore(
            max_entries=resolve_budget_int("coordination_metrics_max_entries"),
        )
    trust_service = overrides.trust_service
    if trust_service is None:
        trust_service = _build_configured_trust_service(effective_config.trust)
    autonomy_change_strategy = _build_configured_autonomy_change_strategy(
        effective_config.config.autonomy,
    )

    # One boot clock shared between the uptime baseline and AppState so
    # ``app_state.clock`` and ``startup_time`` cannot diverge, and a FakeClock
    # injected via AppState in tests governs both.
    boot_clock = SystemClock()
    app_state = AppState(
        clock=boot_clock,
        config=effective_config,
        startup_time=boot_clock.monotonic(),
    )
    # Compose every feature's (empty) state slice up front so the
    # construction-phase wiring below has a slice to ``model_copy`` from. The
    # startup hook re-runs this idempotently (skips already-composed slices).
    compose_feature_slices(app_state)
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    # Opaque pagination cursor HMAC secret. Rolling with a random per-process
    # key silently invalidates every client cursor on every restart, so the
    # refuse-to-boot guard below runs unconditionally.
    cursor_secret = CursorSecret.from_config(CursorConfig.from_env())

    # Construction-phase service bundle handed to every feature's
    # ``construction_wirer``; ``run_construction_wiring`` invokes the wirers in
    # dependency order so each feature populates its own slice.
    construction_deps = ConstructionDeps(
        effective_config=effective_config,
        phase1=phase1,
        meeting_wire=meeting_wire,
        integrations=integrations,
        approval_store=approval_store,
        autonomy_change_strategy=autonomy_change_strategy,
        notification_dispatcher=notification_dispatcher,
        event_stream_hub=overrides.event_stream_hub or EventStreamHub(),
        interrupt_store=overrides.interrupt_store or InterruptStore(),
        cursor_secret=cursor_secret,
        persistence=persistence,
        settings_service=overrides.settings_service,
        auth_service=overrides.auth_service,
        audit_log=audit_log,
        trust_service=trust_service,
        coordination_metrics_store=coordination_metrics_store,
        performance_tracker=performance_tracker,
        agent_registry=agent_registry,
        training_service=overrides.training_service,
        delegation_record_store=overrides.delegation_record_store,
        tool_invocation_tracker=overrides.tool_invocation_tracker,
        artifact_storage=boot.artifact_storage,
        coordinator=overrides.coordinator,
        work_pipeline=overrides.work_pipeline,
        intake_entry_adapter=overrides.intake_entry_adapter,
        task_board_entry_adapter=overrides.task_board_entry_adapter,
        client_simulation_state=overrides.client_simulation_state,
    )
    run_construction_wiring(app_state, construction_deps)

    # Compose the config resolver + management / org-mutation / audit / preset
    # services when a settings service is injected at build time (a no-op
    # otherwise -- the startup ``auto_wire_settings`` hook composes them once
    # persistence connects).
    from synthorg.api.lifecycle_helpers.settings_dependent_services import (  # noqa: PLC0415
        compose_settings_dependent_services,
    )

    compose_settings_dependent_services(app_state, overrides.settings_service)
    if cursor_secret.is_ephemeral:
        msg = (
            "refusing to start with an ephemeral pagination cursor secret; set "
            "SYNTHORG_PAGINATION_CURSOR_SECRET to a stable value (>= 16 bytes)."
        )
        logger.error(
            API_APP_STARTUP,
            note="refusing startup: ephemeral pagination cursor secret",
        )
        raise RuntimeError(msg)

    # Communication-domain services + human-escalation queue. The escalation
    # notify subscriber reads the settings config resolver composed just above,
    # so this runs after ``compose_settings_dependent_services``.
    if message_bus is not None and persistence is not None:
        from synthorg.communication.messages.service import (  # noqa: PLC0415
            MessageService,
        )

        app_state.wire(
            CommunicationStateSlice,
            message_service=MessageService(bus=message_bus, persistence=persistence),
        )
    if meeting_orchestrator is not None:
        from synthorg.communication.meetings.service import (  # noqa: PLC0415
            MeetingService,
        )

        app_state.wire(
            CommunicationStateSlice,
            meeting_service=MeetingService(orchestrator=meeting_orchestrator),
        )

    escalation_config = effective_config.communication.conflict_resolution.escalation
    escalation_store = build_escalation_queue_store(escalation_config, persistence)
    escalation_registry = PendingFuturesRegistry()
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    app_state.wire(
        CommunicationStateSlice,
        escalation_store=escalation_store,
        escalation_processor=build_decision_processor(escalation_config),
        escalation_registry=escalation_registry,
        escalation_sweeper=EscalationExpirationSweeper(
            escalation_store,
            interval_seconds=escalation_config.sweeper_interval_seconds,
        ),
        escalation_notify_subscriber=build_escalation_notify_subscriber(
            escalation_config,
            escalation_store,
            escalation_registry,
            reconnect_delay_seconds=escalation_config.reconnect_delay_seconds,
            config_resolver=config_resolver,
        ),
    )

    bridge = (
        MessageBusBridge(
            message_bus,
            channels_plugin,
            config_resolver=config_resolver,
        )
        if message_bus is not None
        else None
    )
    backup_service = build_backup_service(
        effective_config,
        resolved_db_path=boot.resolved_db_path,
        resolved_config_path=boot.resolved_config_path,
    )
    # ``_build_settings_dispatcher`` wires the timeout-check subscriber onto the
    # scheduler, so the scheduler must exist before the dispatcher is built.
    approval_timeout_scheduler = build_default_approval_timeout_scheduler(
        approval_store=approval_store,
    )
    settings_dispatcher = _build_settings_dispatcher(
        message_bus,
        overrides.settings_service,
        effective_config,
        app_state,
        backup_service,
        approval_timeout_scheduler,
    )
    plugins: list[ChannelsPlugin] = [channels_plugin]
    rate_limiter_enabled = resolve_rate_limiter_enabled()
    if not rate_limiter_enabled:
        logger.warning(
            API_APP_STARTUP,
            note=(
                "global rate limiter disabled by api.rate_limiter_enabled;"
                " do not deploy this configuration to production"
            ),
        )
    middleware = _build_middleware(
        api_config,
        a2a_enabled=effective_config.a2a.enabled,
        rate_limiter_enabled=rate_limiter_enabled,
    )

    return ConstructionResult(
        app_state=app_state,
        message_bus=message_bus,
        cost_tracker=phase1.cost_tracker,
        task_engine=phase1.task_engine,
        provider_registry=provider_registry,
        meeting_scheduler=meeting_scheduler,
        connection_catalog=integrations.connection_catalog,
        notification_dispatcher=notification_dispatcher,
        performance_tracker=performance_tracker,
        approval_store=approval_store,
        bridge=bridge,
        settings_dispatcher=settings_dispatcher,
        backup_service=backup_service,
        approval_timeout_scheduler=approval_timeout_scheduler,
        plugins=plugins,
        middleware=middleware,
        should_auto_wire_settings=(
            overrides.settings_service is None and persistence is not None
        ),
    )
