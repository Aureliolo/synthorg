"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import sys
from typing import TYPE_CHECKING

from litestar import Litestar, Router

from synthorg.api.app_builders import (
    _bootstrap_app_logging,
    _build_configured_autonomy_change_strategy,
    _build_configured_trust_service,
    _build_performance_tracker,
)
from synthorg.api.app_helpers import (
    _make_expire_callback,
    _make_meeting_publisher,
)
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.controller_helpers import require_password_changed
from synthorg.api.auto_wire import (
    auto_wire_meetings,
    auto_wire_phase1,
)
from synthorg.api.boot_persistence import resolve_boot_persistence
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import (
    create_channels_plugin,
)
from synthorg.api.construction_wiring import (
    ConstructionDeps,
    run_construction_wiring,
)
from synthorg.api.cursor import CursorSecret
from synthorg.api.cursor_config import CursorConfig
from synthorg.api.feature_composition import (
    collect_route_handlers,
    compose_feature_slices,
)
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle_assembly import assemble_lifespan_hooks
from synthorg.api.lifecycle_helpers.boot_resolvers import (
    build_default_approval_timeout_scheduler,
    resolve_budget_int,
    resolve_rate_limiter_enabled,
)
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.litestar_assembly import build_litestar
from synthorg.api.middleware_factory import _build_middleware
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.backup.factory import build_backup_service
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.communication.conflict_resolution.escalation import (
    EscalationExpirationSweeper,
    PendingFuturesRegistry,
    build_decision_processor,
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.schema import RootConfig
from synthorg.core.clock import SystemClock
from synthorg.hr.registry import AgentRegistryService
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import (
    get_logger,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.security.audit import AuditLog

if TYPE_CHECKING:
    from litestar.channels import ChannelsPlugin


logger = get_logger(__name__)


# Construction bakes immutable middleware / CORS / routes from RootConfig;
# on_startup wires SettingsService + ConfigResolver for runtime-editable
# settings. Litestar rate-limit middleware reads config at construction;
# runtime DB changes only affect code calling get_api_config(). Boot-time
# setting resolvers + the default approval-timeout scheduler live in
# ``lifecycle_helpers/boot_resolvers.py``.


def create_app(
    *,
    config: RootConfig | None = None,
    overrides: AppOverrides | None = None,
    _skip_lifecycle_shutdown: bool = False,
) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        config: Root company configuration.
        overrides: Optional dependency injections (chiefly tests / bespoke
            wiring); any field left unset is auto-wired from config and the
            environment. An injected double always wins over the auto-wired one.
        _skip_lifecycle_shutdown: Test-only flag. When ``True`` the app is built
            with an empty ``on_shutdown`` list so a shared-app fixture can reuse
            it across lifespans without tearing down the task engine, message
            bus, and persistence. Never use in production: shutdown hooks
            perform critical cleanup.

    Returns:
        Configured Litestar application.
    """
    ov = overrides or AppOverrides()
    persistence = ov.persistence
    message_bus = ov.message_bus
    cost_tracker = ov.cost_tracker
    approval_store = ov.approval_store
    auth_service = ov.auth_service
    task_engine = ov.task_engine
    coordinator = ov.coordinator
    work_pipeline = ov.work_pipeline
    intake_entry_adapter = ov.intake_entry_adapter
    task_board_entry_adapter = ov.task_board_entry_adapter
    agent_registry = ov.agent_registry
    meeting_orchestrator = ov.meeting_orchestrator
    meeting_scheduler = ov.meeting_scheduler
    performance_tracker = ov.performance_tracker
    settings_service = ov.settings_service
    provider_registry = ov.provider_registry
    provider_health_tracker = ov.provider_health_tracker
    tool_invocation_tracker = ov.tool_invocation_tracker
    delegation_record_store = ov.delegation_record_store
    artifact_storage = ov.artifact_storage
    audit_log = ov.audit_log
    trust_service = ov.trust_service
    coordination_metrics_store = ov.coordination_metrics_store
    training_service = ov.training_service
    event_stream_hub = ov.event_stream_hub
    interrupt_store = ov.interrupt_store
    client_simulation_state = ov.client_simulation_state

    effective_config = config or RootConfig(company_name="default")

    # Activate the structured logging pipeline before any
    # other setup so that auto-wiring, persistence, and bus logs all
    # flow through the configured sinks.  Respects SYNTHORG_LOG_DIR
    # env var for Docker log directory override.
    try:
        effective_config = _bootstrap_app_logging(effective_config)
    except Exception as exc:
        print(  # noqa: T201
            f"CRITICAL: Failed to initialise logging pipeline: {safe_error_description(exc)}. "  # noqa: E501
            "Check SYNTHORG_LOG_DIR, SYNTHORG_LOG_LEVEL, and the "
            "'logging' section of your config file.",
            file=sys.stderr,
            flush=True,
        )
        raise

    api_config = effective_config.api

    # Auto-wire persistence + artifact storage from the CLI-provided env vars
    # (unless injected); the raw env values flow through for downstream wiring.
    boot = resolve_boot_persistence(
        persistence=persistence,
        artifact_storage=artifact_storage,
    )
    persistence = boot.persistence
    artifact_storage = boot.artifact_storage
    resolved_db_path = boot.resolved_db_path
    resolved_config_path = boot.resolved_config_path
    db_url = boot.db_url
    db_path = boot.db_path

    # ── Construction-time auto-wire: services that don't need connected persistence ──
    phase1 = auto_wire_phase1(
        effective_config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
        cost_tracker=cost_tracker,
        task_engine=task_engine,
        provider_registry=provider_registry,
        provider_health_tracker=provider_health_tracker,
    )
    message_bus = phase1.message_bus
    cost_tracker = phase1.cost_tracker
    task_engine = phase1.task_engine
    provider_registry = phase1.provider_registry
    provider_health_tracker = phase1.provider_health_tracker

    # Pre-meetings; versioning wires on startup once persistence.connect() runs.
    if agent_registry is None:
        agent_registry = AgentRegistryService()
        logger.info(API_SERVICE_AUTO_WIRED, service="agent_registry")

    # ── Meeting auto-wire: orchestrator + scheduler (construction-time) ──
    meeting_wire = auto_wire_meetings(
        effective_config=effective_config,
        meeting_orchestrator=meeting_orchestrator,
        meeting_scheduler=meeting_scheduler,
        agent_registry=agent_registry,
        provider_registry=provider_registry,
        persistence=persistence,
    )
    meeting_orchestrator = meeting_wire.meeting_orchestrator
    meeting_scheduler = meeting_wire.meeting_scheduler
    ceremony_scheduler = meeting_wire.ceremony_scheduler

    channels_plugin = create_channels_plugin()
    expire_callback = _make_expire_callback(channels_plugin)
    effective_approval_store: ApprovalStoreProtocol = (
        approval_store
        if approval_store is not None
        else ApprovalStore(on_expire=expire_callback)
    )

    # Wire meeting event publisher to the meetings WS channel.
    if meeting_scheduler is not None and meeting_scheduler._event_publisher is None:  # noqa: SLF001
        meeting_scheduler._event_publisher = _make_meeting_publisher(  # noqa: SLF001
            channels_plugin,
        )

    # Auto-wire performance tracker with composite quality strategy
    # when not explicitly injected (production path).
    if performance_tracker is None:
        performance_tracker = _build_performance_tracker(
            cost_tracker=cost_tracker,
            provider_registry=provider_registry,
            perf_config=effective_config.performance,
        )

    notification_dispatcher = build_notification_dispatcher(
        effective_config.notifications,
    )

    # -- Integration services auto-wire ──────────────────────────────────
    integrations = auto_wire_integrations(
        effective_config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
        api_config=api_config,
        ceremony_scheduler=ceremony_scheduler,
        db_url=db_url,
        resolved_db_path=resolved_db_path,
        boot_db_path=db_path,
    )
    connection_catalog = integrations.connection_catalog

    # Auto-wire control-plane services when not injected.
    if audit_log is None:
        audit_log = AuditLog()
    if coordination_metrics_store is None:
        coordination_metrics_store = CoordinationMetricsStore(
            max_entries=resolve_budget_int("coordination_metrics_max_entries"),
        )
    if trust_service is None:
        trust_service = _build_configured_trust_service(effective_config.trust)
    autonomy_change_strategy = _build_configured_autonomy_change_strategy(
        effective_config.config.autonomy,
    )

    # One boot clock shared between the uptime baseline and AppState so
    # ``app_state.clock`` and ``startup_time`` cannot diverge, and a
    # FakeClock injected via AppState in tests governs both.
    _boot_clock = SystemClock()
    event_stream_hub = event_stream_hub or EventStreamHub()
    interrupt_store = interrupt_store or InterruptStore()
    # Thin construction: AppState holds only config / clock / startup_time
    # + the cross-cutting primitives. Every service flows into its
    # feature slice via the ``swap_slice`` composition block below.
    app_state = AppState(
        clock=_boot_clock,
        config=effective_config,
        startup_time=_boot_clock.monotonic(),
    )
    # Compose every feature's (empty) state slice up front so the
    # construction-phase wiring below and the lazy-wire ``_swap_*``
    # shims have a slice to ``model_copy`` from. The startup hook
    # re-runs this idempotently (skips already-composed slices).
    compose_feature_slices(app_state)
    from synthorg.communication.state import (  # noqa: PLC0415
        CommunicationStateSlice,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    # Opaque pagination cursor HMAC secret. Loaded from the
    # ``SYNTHORG_PAGINATION_CURSOR_SECRET`` env var; rolling with a random
    # per-process key silently invalidates every client cursor on every
    # restart, which is a correctness defect, not a warning. The
    # refuse-to-boot guard below runs unconditionally so this latent
    # failure can never hide behind a "looks fine in dev" code path.
    cursor_secret = CursorSecret.from_config(CursorConfig.from_env())

    # Construction-phase service bundle handed to every feature's
    # ``construction_wirer``. Each feature populates its own state slice from
    # these already-built services; ``run_construction_wiring`` invokes the
    # wirers in dependency order (so ``communication`` reads the ``settings``
    # config resolver only after ``settings`` wired it).
    construction_deps = ConstructionDeps(
        effective_config=effective_config,
        phase1=phase1,
        meeting_wire=meeting_wire,
        integrations=integrations,
        approval_store=effective_approval_store,
        autonomy_change_strategy=autonomy_change_strategy,
        notification_dispatcher=notification_dispatcher,
        event_stream_hub=event_stream_hub,
        interrupt_store=interrupt_store,
        cursor_secret=cursor_secret,
        persistence=persistence,
        settings_service=settings_service,
        auth_service=auth_service,
        audit_log=audit_log,
        trust_service=trust_service,
        coordination_metrics_store=coordination_metrics_store,
        performance_tracker=performance_tracker,
        agent_registry=agent_registry,
        training_service=training_service,
        delegation_record_store=delegation_record_store,
        tool_invocation_tracker=tool_invocation_tracker,
        artifact_storage=artifact_storage,
        coordinator=coordinator,
        work_pipeline=work_pipeline,
        intake_entry_adapter=intake_entry_adapter,
        task_board_entry_adapter=task_board_entry_adapter,
        client_simulation_state=client_simulation_state,
    )
    run_construction_wiring(app_state, construction_deps)

    # Compose the config resolver + management / org-mutation / audit /
    # preset services when a settings service is injected at build time
    # (a no-op when no settings service is provided, where the startup
    # ``auto_wire_settings`` hook composes them once persistence connects).
    from synthorg.api.lifecycle_helpers.settings_dependent_services import (  # noqa: PLC0415
        compose_settings_dependent_services,
    )

    compose_settings_dependent_services(app_state, settings_service)
    if cursor_secret.is_ephemeral:
        msg = (
            "refusing to start with an ephemeral pagination cursor "
            "secret; set SYNTHORG_PAGINATION_CURSOR_SECRET to a stable "
            "value (>= 16 bytes)."
        )
        # Emit the structured error before raising so centralized log
        # collectors see the refusal reason even if the caller swallows
        # or reformats the exception message.
        logger.error(
            API_APP_STARTUP,
            note="refusing startup: ephemeral pagination cursor secret",
        )
        raise RuntimeError(msg)

    # Human escalation approval queue. Builds the pluggable store +
    # processor + Future registry and attaches them to ``AppState``
    # so the escalations controller and the
    # ``HumanEscalationResolver`` share a single instance.
    escalation_config = effective_config.communication.conflict_resolution.escalation
    _escalation_store = build_escalation_queue_store(
        escalation_config,
        persistence,
    )
    # Communication-domain services (messages + meetings).
    # Both wrap pre-existing infrastructure (bus + persistence,
    # orchestrator) and centralize audit logging so HTTP controllers
    # and MCP handlers route through a single facade per
    # `docs/reference/conventions.md` § Repository CRUD pattern. The
    # MCP handlers in `meta/mcp/handlers/communication.py` already
    # call `app_state.message_service` / `app_state.meeting_service`,
    # so wiring here is what activates them in production.
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

    _escalation_registry = PendingFuturesRegistry()
    # Cross-instance wake-up subscriber. No-op unless the queue
    # backend is Postgres and ``cross_instance_notify`` is enabled;
    # otherwise the sweeper and per-resolver timeout cover eventual
    # consistency on their own.
    app_state.wire(
        CommunicationStateSlice,
        escalation_store=_escalation_store,
        escalation_processor=build_decision_processor(escalation_config),
        escalation_registry=_escalation_registry,
        escalation_sweeper=EscalationExpirationSweeper(
            _escalation_store,
            interval_seconds=escalation_config.sweeper_interval_seconds,
        ),
        escalation_notify_subscriber=build_escalation_notify_subscriber(
            escalation_config,
            _escalation_store,
            _escalation_registry,
            reconnect_delay_seconds=escalation_config.reconnect_delay_seconds,
            config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        ),
    )

    bridge = (
        MessageBusBridge(
            message_bus,
            channels_plugin,
            config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        )
        if message_bus is not None
        else None
    )
    backup_service = build_backup_service(
        effective_config,
        resolved_db_path=resolved_db_path,
        resolved_config_path=resolved_config_path,
    )
    # ``_build_settings_dispatcher`` needs the scheduler instance to
    # wire the ``security.timeout_check_interval_seconds`` subscriber,
    # so the scheduler must be in scope before the dispatcher is built.
    approval_timeout_scheduler = build_default_approval_timeout_scheduler(
        approval_store=effective_approval_store,
    )
    settings_dispatcher = _build_settings_dispatcher(
        message_bus,
        settings_service,
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

    # Integration controllers are discovery-registered from the
    # integrations feature manifest. Each carries a readiness predicate
    # (see ``api.route_predicates``) that short-circuits on
    # ``integrations.enabled`` being false -- preserving both the
    # ~0.7s-per-create_app registration saving when the subsystem is off
    # and the per-collaborator 404 gate (catalog / bus / tunnel-provider)
    # when it is on.

    # Route registration is discovery-based: collect every feature
    # manifest's controllers (api-mounted vs root-mounted) and websocket
    # handlers, evaluating each ControllerRegistration predicate against
    # the constructed AppState so a disabled or unwired subsystem's routes
    # are not registered at all (404), exactly as the hand-maintained
    # lists did. /capabilities reports which subsystems are wired so the
    # dashboard skips polling at the source.
    api_handlers, root_handlers = collect_route_handlers(app_state)
    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=api_handlers,
        guards=[require_password_changed],
    )

    # Startup auto-wiring flag: persistence being non-None is the
    # enabling condition -- SettingsService needs connected persistence
    # and is created in on_startup after _init_persistence().
    _should_auto_wire = settings_service is None and persistence is not None

    # ``approval_timeout_scheduler`` is built above (alongside the
    # backup service and bridge); the lifecycle owns starting it.
    # ``_apply_security_timeout_interval`` in ``lifecycle_helpers.py``
    # resolves the operator-tuned interval from ``ConfigResolver`` after
    # persistence connects and calls ``scheduler.reschedule(...)`` so the
    # configured cadence takes effect on the next loop tick. Default
    # policy is ``WaitForeverPolicy``: the scheduler runs but never
    # auto-decides. Operators swap in DenyOnTimeout / Tiered /
    # EscalationChain via the security.* settings at runtime.

    startup, shutdown = assemble_lifespan_hooks(
        app_state,
        persistence=persistence,
        message_bus=message_bus,
        bridge=bridge,
        settings_dispatcher=settings_dispatcher,
        task_engine=task_engine,
        meeting_scheduler=meeting_scheduler,
        backup_service=backup_service,
        approval_timeout_scheduler=approval_timeout_scheduler,
        should_auto_wire_settings=_should_auto_wire,
        effective_config=effective_config,
        connection_catalog=connection_catalog,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        approval_store=effective_approval_store,
        performance_tracker=performance_tracker,
        notification_dispatcher=notification_dispatcher,
    )

    if _skip_lifecycle_shutdown:
        shutdown = []

    return build_litestar(
        app_state,
        api_config=api_config,
        api_router=api_router,
        root_handlers=root_handlers,
        middleware=middleware,
        plugins=plugins,
        startup=startup,
        shutdown=shutdown,
        skip_lifecycle_shutdown=_skip_lifecycle_shutdown,
    )
