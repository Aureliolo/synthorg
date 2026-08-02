# module-kind: orchestrator
"""Construction-phase service build for the composition root.

``create_app`` delegates the persistence-independent service construction to
:func:`build_construction_services`: it builds ``AppState``, auto-wires the
construction-phase / meeting / integration services, composes every feature's
state slice, runs each feature's ``construction_wirer`` (via
``run_construction_wiring``),
wires the communication-domain services + escalation stack, builds the
bridge / backup / settings-dispatcher / middleware, and returns the handful of
collaborators the composition root threads into route assembly, the lifespan
hooks, and the Litestar build. Keeping it here leaves ``create_app`` a thin
orchestrator (bootstrap -> persistence -> build -> routes -> lifespan -> app).
"""

from dataclasses import dataclass

from litestar.channels import ChannelsPlugin
from litestar.types import Middleware

from synthorg.api._comms_conflict_wiring import wire_conflict_resolution_service
from synthorg.api.app_builders import (
    _build_configured_autonomy_change_strategy,
    _build_performance_tracker,
)
from synthorg.api.app_helpers import (
    _make_expire_callback,
    _make_meeting_publisher,
    make_steering_notifier,
)
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
from synthorg.api.integrations_wiring import (
    auto_wire_integrations,
    wire_rate_limit_coordinator_factory,
)
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
from synthorg.backup.state import BackupStateSlice
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.conflict_resolution.escalation.factory import (
    build_decision_processor,
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,
)
from synthorg.communication.delegation.record_store import DelegationRecordStore
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP, API_SERVICE_AUTO_WIRED
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_int
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.invocation_tracker import ToolInvocationTracker

logger = get_logger(__name__)


@dataclass(frozen=True)
class ConstructionResult:
    """The collaborators the composition root needs after the build phase.

    ``app_state`` is fully slice-populated; the remaining fields feed route
    assembly, the lifespan-hook assembly, and the Litestar build.
    """

    app_state: AppState
    message_bus: MessageBus | None
    cost_tracker: CostTrackerProtocol | None
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


def _wire_quadratic_alert_sink(
    message_bus: MessageBus | None,
    dispatcher: NotificationDispatcher,
) -> None:
    """Late-bind the bus quadratic enforcer's alert sink to the dispatcher.

    The bus is constructed before the dispatcher exists, so its enforcer
    (if any) starts with no alert sink (it still emits the structured
    detection event). Once the dispatcher is built we wrap it in a
    :class:`DispatcherQuadraticAlertSink` so quadratic detections also
    fire operator notifications. The bus routes the sink to its enforcer
    through the ``MessageBus`` protocol seam, so backends without an
    enforcer (NATS) or with enforcement disabled absorb it as a no-op
    without the wiring code naming a concrete backend.

    Args:
        message_bus: The auto-wired message bus, or ``None``.
        dispatcher: The construction-phase notification dispatcher.
    """
    if message_bus is None:
        return
    from synthorg.notifications.quadratic_alert_sink import (  # noqa: PLC0415
        DispatcherQuadraticAlertSink,
    )

    message_bus.set_quadratic_alert_sink(
        DispatcherQuadraticAlertSink(dispatcher=dispatcher)
    )


def _wire_communication_services(
    app_state: AppState,
    *,
    effective_config: RootConfig,
    message_bus: MessageBus | None,
    persistence: PersistenceBackend | None,
    meeting_orchestrator: MeetingOrchestrator | None,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
    agent_registry: AgentRegistryService,
) -> ConfigResolver | None:
    """Wire the communication-domain + human-escalation services onto AppState.

    Runs AFTER ``compose_settings_dependent_services`` so the escalation notify
    subscriber can read the settings config resolver it composes. Mutates the
    ``CommunicationStateSlice`` in place (the established wiring pattern) and
    returns the resolver so the caller can thread it into the message-bus bridge.

    Args:
        app_state: The slice-populated application state to wire onto.
        effective_config: The resolved root configuration.
        message_bus: The auto-wired message bus, or ``None``.
        persistence: The persistence backend (may be ``None``).
        meeting_orchestrator: The auto-wired meeting orchestrator, or ``None``.
        provider_registry: The provider registry, so the conflict-resolution
            service can build its LLM judge (``None`` -> no judge).
        cost_tracker: The cost tracker the LLM judge attributes spend to.
        agent_registry: The agent registry the meeting-conflict bridge reads
            department/role from to build conflict positions.

    Returns:
        The settings ``config_resolver`` for the bridge and later consumers.
    """
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

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

    cr_config = effective_config.communication.conflict_resolution
    escalation_config = cr_config.escalation
    escalation_store = build_escalation_queue_store(escalation_config, persistence)
    escalation_registry = PendingFuturesRegistry()
    escalation_processor = build_decision_processor(escalation_config)
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    app_state.wire(
        CommunicationStateSlice,
        escalation_store=escalation_store,
        escalation_processor=escalation_processor,
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
    wire_conflict_resolution_service(
        app_state,
        effective_config=effective_config,
        config=cr_config,
        message_bus=message_bus,
        escalation_store=escalation_store,
        escalation_processor=escalation_processor,
        escalation_registry=escalation_registry,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    _wire_meeting_conflict_bridge(
        app_state,
        meeting_orchestrator=meeting_orchestrator,
        agent_registry=agent_registry,
        config_resolver=config_resolver,
    )
    return config_resolver


def _wire_meeting_conflict_bridge(
    app_state: AppState,
    *,
    meeting_orchestrator: MeetingOrchestrator | None,
    agent_registry: AgentRegistryService,
    config_resolver: ConfigResolver | None,
) -> None:
    """Install the meeting-to-conflict-resolution bridge on the orchestrator.

    The conflict-resolution service is built later in this same wiring pass
    than the orchestrator is constructed, so the bridge is installed via the
    orchestrator's setter here. The bridge is also stored on the slice so the
    startup resolver rebind (``_wire_resolver_dependents``) can reach it. A
    no-op when either the orchestrator or the service is absent.
    """
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415

    conflict_service = app_state.slice(
        CommunicationStateSlice
    ).conflict_resolution_service
    if meeting_orchestrator is None or conflict_service is None:
        return
    from synthorg.communication.meeting.conflict_escalation import (  # noqa: PLC0415
        MeetingConflictEscalationBridge,
    )

    bridge = MeetingConflictEscalationBridge(
        conflict_service=conflict_service,
        agent_registry=agent_registry,
        config_resolver=config_resolver,
    )
    meeting_orchestrator.set_conflict_escalation_hook(bridge)
    app_state.wire(
        CommunicationStateSlice,
        conflict_escalation_bridge=bridge,
    )


def build_construction_services(
    *,
    effective_config: RootConfig,
    api_config: ApiConfig,
    overrides: AppOverrides,
    boot: BootPersistence,
    clock: Clock | None = None,
) -> ConstructionResult:
    """Build the persistence-independent services and the populated AppState.

    Args:
        effective_config: The resolved root configuration.
        api_config: The resolved API configuration.
        overrides: Caller-injected dependency doubles (``None`` -> auto-wire).
        boot: The resolved persistence backend, artifact storage, and paths.
        clock: Optional clock seam backing both ``app_state.clock`` and the
            ``startup_time`` baseline so a test can drive a deterministic boot;
            defaults to ``SystemClock``.

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
    _wire_quadratic_alert_sink(message_bus, notification_dispatcher)

    integrations = auto_wire_integrations(
        effective_config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
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
    autonomy_change_strategy = _build_configured_autonomy_change_strategy(
        effective_config.config.autonomy,
    )

    # One boot clock shared between the uptime baseline and AppState so
    # ``app_state.clock`` and ``startup_time`` cannot diverge. The optional
    # ``clock`` seam lets a caller inject a FakeClock for a deterministic boot
    # (tests); it defaults to ``SystemClock`` when not supplied.
    boot_clock = clock if clock is not None else SystemClock()
    app_state = AppState(
        clock=boot_clock,
        config=effective_config,
        startup_time=boot_clock.monotonic(),
        boot_at=boot_clock.now(),
    )
    # Compose every feature's (empty) state slice up front so the
    # construction-phase wiring below has a slice to ``model_copy`` from. The
    # startup hook re-runs this idempotently (skips already-composed slices).
    compose_feature_slices(app_state)

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
        event_stream_hub=overrides.event_stream_hub
        or EventStreamHub(
            max_queue_size=int(
                resolve_init_value(
                    SettingNamespace.COMMUNICATION,
                    "event_stream_max_queue_size",
                    parse=parse_int,
                ).value
            ),
            history_per_session=int(
                resolve_init_value(
                    SettingNamespace.COMMUNICATION,
                    "event_stream_history_per_session",
                    parse=parse_int,
                ).value
            ),
            history_max_sessions=int(
                resolve_init_value(
                    SettingNamespace.COMMUNICATION,
                    "event_stream_history_max_sessions",
                    parse=parse_int,
                ).value
            ),
        ),
        interrupt_store=overrides.interrupt_store or InterruptStore(),
        cursor_secret=cursor_secret,
        persistence=persistence,
        # Persistence is "expected" whenever the operator pointed the boot
        # at a backend (SYNTHORG_DATABASE_URL / SYNTHORG_DB_PATH); a later
        # missing/unconnected backend is then a real readiness failure
        # rather than a deliberately persistence-less dev run.
        persistence_expected=bool(boot.db_url or boot.db_path),
        settings_service=overrides.settings_service,
        auth_service=overrides.auth_service,
        audit_log=audit_log,
        coordination_metrics_store=coordination_metrics_store,
        performance_tracker=performance_tracker,
        agent_registry=agent_registry,
        training_service=overrides.training_service,
        delegation_record_store=overrides.delegation_record_store
        or DelegationRecordStore(
            max_records=int(
                resolve_init_value(
                    SettingNamespace.COMMUNICATION,
                    "delegation_record_store_max_size",
                    parse=parse_int,
                ).value
            ),
        ),
        tool_invocation_tracker=overrides.tool_invocation_tracker
        or ToolInvocationTracker(),
        artifact_storage=boot.artifact_storage,
        coordinator=overrides.coordinator,
        work_pipeline=overrides.work_pipeline,
        intake_entry_adapter=overrides.intake_entry_adapter,
        task_board_entry_adapter=overrides.task_board_entry_adapter,
        client_simulation_state=overrides.client_simulation_state,
    )
    run_construction_wiring(app_state, construction_deps)

    # The shared rate-limit coordinator factory reads the live
    # ``ApiBridgeConfig`` snapshot per connection, so it is wired here,
    # after ``app_state`` exists: the per-connection closure runs
    # post-startup when the resolved + hot-swapped snapshot is in place.
    if message_bus is not None and integrations.connection_catalog is not None:
        wire_rate_limit_coordinator_factory(
            message_bus=message_bus,
            connection_catalog=integrations.connection_catalog,
            app_state=app_state,
        )

    # The cockpit-channel steering notifier closes over the channels plugin
    # (a construction-phase artifact) and is parked on the cockpit slice so
    # ``wire_steering_service`` can inject it once the steering service wires
    # after the project brain connects.
    from synthorg.engine.cockpit.state import CockpitStateSlice  # noqa: PLC0415

    app_state.wire(
        CockpitStateSlice,
        steering_notifier=make_steering_notifier(channels_plugin),
    )

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
    config_resolver = _wire_communication_services(
        app_state,
        effective_config=effective_config,
        message_bus=message_bus,
        persistence=persistence,
        meeting_orchestrator=meeting_orchestrator,
        provider_registry=provider_registry,
        cost_tracker=phase1.cost_tracker,
        agent_registry=agent_registry,
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
        config_resolver=config_resolver,
        # The env-driven backend never reaches ``effective_config``, so without
        # this the handler is built from config fields the deployment left at
        # their defaults: it would target a database nothing is using, and for
        # Postgres it has no connection details to target at all.
        boot_backend=persistence,
        # Recorded here rather than left in the log so ``/health`` can name the
        # fault instead of only its absence.
        on_unavailable=lambda reason: app_state.wire(
            BackupStateSlice,
            unavailable_reason=reason,
        ),
    )
    # ``_build_settings_dispatcher`` wires the timeout-check subscriber onto the
    # scheduler, so the scheduler must exist before the dispatcher is built.
    approval_timeout_scheduler = build_default_approval_timeout_scheduler(
        approval_store=approval_store,
        approval_timeout_config=effective_config.config.approval_timeout,
    )
    settings_dispatcher = _build_settings_dispatcher(
        message_bus=message_bus,
        settings_service=overrides.settings_service,
        config=effective_config,
        app_state=app_state,
        backup_service=backup_service,
        approval_timeout_scheduler=approval_timeout_scheduler,
    )
    plugins: list[ChannelsPlugin] = [channels_plugin]
    if not resolve_rate_limiter_enabled():
        # The tiers are mounted either way and consult the flag per
        # request, so this is a warning about the value the process
        # started with, not about a stack that can never enforce.
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
