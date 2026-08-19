# module-kind: code
"""On-startup / on-shutdown lifespan-hook assembly for the composition root.

Wraps the core-scaffold lifecycle (``_build_lifecycle``) and appends the
feature-wiring, runtime-services, telemetry, report, toolsmith, notification,
and security-settings hooks in the order the running app needs, keeping
``create_app`` a thin orchestrator that hands over the already-built
collaborators.

The ordering is load-bearing (see ADR-0008's core-scaffold note):
``_compose_feature_slices`` runs first so every feature's empty slice exists
before any wiring hook; ``_install_runtime_services`` runs immediately after
the core startup hooks (which connect persistence and wire SettingsService) so
its once-only ``set_*`` calls cannot lose the race with the worker property's
lazy default; telemetry / notification start and security-settings resolve run
last; telemetry shutdown is appended so it reflects final state after the
load-bearing teardown completes.
"""

from collections.abc import Awaitable, Callable

from synthorg.api.app_builders import _build_telemetry_collector
from synthorg.api.app_helpers import resolve_agent_workspace_root_env
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.feature_composition import compose_feature_slices
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.api.lifecycle_helpers.ask_policy_wiring import wire_ask_policy
from synthorg.api.lifecycle_helpers.feature_lifecycle import (
    build_feature_lifecycle_runner,
)
from synthorg.api.lifecycle_helpers.output_style_wiring import (
    wire_output_style_policy,
)
from synthorg.api.lifecycle_helpers.provider_registry_reload import (
    reload_persisted_provider_registry_for_boot,
)
from synthorg.api.lifecycle_helpers.startup_steps import (
    install_runtime_services,
    resolve_runtime_security_settings,
    wire_brownfield_intake,
)
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.budget.automated_reports import AutomatedReportService
from synthorg.budget.reports import ReportGenerator
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice
from synthorg.telemetry.state import TelemetryStateSlice

logger = get_logger(__name__)

type LifespanHooks = list[Callable[[], Awaitable[None]]]


def assemble_lifespan_hooks(  # noqa: PLR0913
    app_state: AppState,
    *,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    should_auto_wire_settings: bool,
    effective_config: RootConfig,
    connection_catalog: ConnectionCatalog | None,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
    performance_tracker: PerformanceTracker | None,
    notification_dispatcher: NotificationDispatcher,
) -> tuple[LifespanHooks, LifespanHooks]:
    """Build the full (on_startup, on_shutdown) lifespan-hook lists.

    Args:
        app_state: The wired application state.
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus.
        bridge: Message-bus bridge to the websocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralised task state engine.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval-timeout checker.
        should_auto_wire_settings: When ``True``, the on-startup auto-wiring
            creates ``SettingsService`` after persistence connects.
        effective_config: Root configuration.
        connection_catalog: Integration connection catalog (runtime services).
        provider_registry: Provider registry (persisted-config reload).
        cost_tracker: Cost tracker (report service).
        performance_tracker: Performance tracker (report service).
        notification_dispatcher: Notification dispatcher (HTTP sink start-up).

    Returns:
        A tuple of (on_startup, on_shutdown) callback lists in run order.
    """
    startup, shutdown = _build_lifecycle(
        persistence=persistence,
        message_bus=message_bus,
        bridge=bridge,
        settings_dispatcher=settings_dispatcher,
        task_engine=task_engine,
        backup_service=backup_service,
        approval_timeout_scheduler=approval_timeout_scheduler,
        app_state=app_state,
        should_auto_wire_settings=should_auto_wire_settings,
        effective_config=effective_config,
    )

    runtime_services_installed = False

    async def _install_runtime_services() -> None:
        nonlocal runtime_services_installed
        if runtime_services_installed:
            return
        # Resolve the agent sandbox workspace root from the deployment env at
        # startup (the install guard runs this once), then inject it into the
        # installer instead of re-reading the environment inside it. Resolving
        # here, not at the composition root, keeps the resolver's fail-fast on
        # a non-absolute SYNTHORG_DB_PATH out of pure app construction (dev /
        # ``:memory:`` / OpenAPI export must not crash on a relative path).
        agent_workspace_root = resolve_agent_workspace_root_env()
        await install_runtime_services(
            app_state,
            connection_catalog=connection_catalog,
            agent_workspace_root=agent_workspace_root,
        )
        runtime_services_installed = True

    brownfield_intake_installed = False

    async def _wire_brownfield_intake() -> None:
        nonlocal brownfield_intake_installed
        if brownfield_intake_installed:
            return
        brownfield_intake_installed = await wire_brownfield_intake(app_state)

    async def _migrate_provider_credentials() -> None:
        from synthorg.api.lifecycle_helpers.provider_credential_migration import (  # noqa: PLC0415
            migrate_embedded_provider_keys,
        )

        await migrate_embedded_provider_keys(app_state)

    async def _adopt_budget_config() -> None:
        # Its own step, not a subsystem's side effect. Adoption used to ride
        # inside the signals facade's activation, which declines on an HR
        # capability that has nothing to do with budget: a boot where that
        # declines adopted nothing and every ceiling stayed on the code
        # default, which is the collapse this exists to close, reached by a
        # different route. Placed after persistence connects (the setting
        # cannot be read before) and before runtime services, which build
        # the enforcer from whatever config is in force by then.
        from synthorg.budget.adoption import (  # noqa: PLC0415
            adopt_resolved_budget_config,
        )

        await adopt_resolved_budget_config(app_state)

    async def _wire_agent_workspace_root() -> None:
        # Runs before EVERY other startup hook (see the list below), because
        # "before the first reconcile pass" is not something a position in the
        # middle of the list can promise: the core hooks reconcile once
        # persistence connects, so a wire placed after them lost the race and
        # the invariant this hook exists for held only in its own comment.
        #
        # `agent_workspace_root_of` falls back to a temp directory when the
        # slice is unset, and `agent_tool_execution` requires no capability, so
        # it activates on that first pass and probed the fallback: on the
        # shipped stack /tmp is a compose `tmpfs:`, which never appears in the
        # container's own `Mounts`, so a perfectly healthy deployment announced
        # that its workspace was unmappable, escalated a blocked-health
        # notification, and activated the same subsystem five seconds later.
        from synthorg.engine.workspace.state import WorkspaceStateSlice  # noqa: PLC0415

        app_state.wire(
            WorkspaceStateSlice,
            agent_workspace_root=resolve_agent_workspace_root_env(),
        )

    async def _reconcile_subsystems() -> None:
        from synthorg.api.subsystems.runtime import (  # noqa: PLC0415
            reconcile_subsystems,
        )

        await reconcile_subsystems(app_state, trigger="boot")

    async def _compose_feature_slices() -> None:
        compose_feature_slices(app_state)

    async def _reload_provider_registry() -> None:
        # A restarted, already-set-up deployment must boot with its
        # DB-persisted providers live: agents are re-bootstrapped at boot,
        # but only ``/setup/complete`` and provider mutations rebuild the
        # registry, so without this reload every provider-gated feature
        # (task execution, chief-of-staff chat, charter, research, ...)
        # stays unwired after a restart. Rebinds the closure variable so
        # every later hook (runtime services, feature wiring, toolsmith,
        # eval loop) sees the reloaded registry. Serving without providers
        # beats refusing to boot: the dashboard is how an operator fixes
        # the configuration, and it needs the API up to reach.
        nonlocal provider_registry
        try:
            reloaded = await reload_persisted_provider_registry_for_boot(app_state)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                service="provider_registry",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="persisted provider reload failed; booting empty-company",
            )
            return
        if reloaded is not None:
            provider_registry = reloaded

    async def _wire_features() -> None:
        # Every optional feature engine is declared in the subsystem registry
        # and brought up by the reconcile passes around this hook. What is
        # left here is the output-style policy and the ask policy, neither of
        # which has a dependency that can arrive late: both are read from
        # settings and installed once.
        await wire_output_style_policy(app_state)
        await wire_ask_policy(app_state)

    # ``_compose_feature_slices`` runs FIRST so every feature's empty state
    # slice exists before any wiring hook (including the persistence-phase
    # ``_safe_startup`` hooks) composes/swaps a populated slice.
    #
    # ``_wire_agent_workspace_root`` runs SECOND, ahead of the core hooks,
    # because those reconcile subsystems the moment persistence connects and
    # the workspace root has to be real by then. It is pure env resolution
    # over an already-composed slice, so nothing else has to precede it.
    startup = [
        _compose_feature_slices,
        _wire_agent_workspace_root,
        *startup,
        # After persistence connects (core hooks above) and before runtime
        # services parse providers: migrate any embedded api_key into the
        # catalog so the resolver does not reject the stored config.
        _migrate_provider_credentials,
        _reload_provider_registry,
        _adopt_budget_config,
        # Memory, org memory and the evolution-outcome store must exist
        # BEFORE runtime services: the engine reads their slices eagerly at
        # construction, so anything wired later never reaches an agent.
        _reconcile_subsystems,
        _install_runtime_services,
        _wire_features,
        # Runtime services bring up the work pipeline, which several
        # subsystems wait on. Running the same pass again costs nothing when
        # nothing moved, and is the whole reason a second call is safe.
        _reconcile_subsystems,
        _wire_brownfield_intake,
    ]

    # Project telemetry collector. Attach to app_state so the health endpoint
    # reports state; start runs in the lifespan, shutdown is appended (runs
    # LAST) so the session-summary event reflects final state after the
    # load-bearing teardown and a hanging flush never blocks cleanup.
    telemetry_collector = _build_telemetry_collector(effective_config.telemetry)
    app_state.swap_slice(TelemetryStateSlice(collector=telemetry_collector))

    async def _apply_telemetry_db_layer() -> None:
        # ``telemetry.enabled`` is DB > env > default, but the collector was
        # built from env > default before persistence connected. Re-resolve
        # with DB awareness now that the resolver is wired and apply it before
        # the collector starts. Best-effort: an unwired resolver (anonymous /
        # test boot) keeps the construction-time value.
        resolver = app_state.slice(SettingsStateSlice).config_resolver
        if resolver is None:
            return
        try:
            enabled = await resolver.get_bool(SettingNamespace.TELEMETRY, "enabled")
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                setting="telemetry.enabled",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        telemetry_collector.apply_resolved_enabled(enabled=enabled)

    startup = [*startup, _apply_telemetry_db_layer, telemetry_collector.start]
    shutdown = [*shutdown, telemetry_collector.shutdown]

    # Automated report service: wired from the cost tracker + budget config so
    # the reports endpoint serves the documented inputs. Risk / performance
    # trackers are optional; with no cost tracker the wire is skipped and the
    # controller honestly 503s.
    if cost_tracker is not None:
        report_generator = ReportGenerator(
            cost_tracker=cost_tracker,
            budget_config=effective_config.budget,
        )
        report_service = AutomatedReportService(
            report_generator=report_generator,
            cost_tracker=cost_tracker,
            risk_tracker=app_state.slice(BudgetStateSlice).risk_tracker,
            performance_tracker=performance_tracker,
        )
        app_state.wire(BudgetStateSlice, report_service=report_service)

    # Held in the closure rather than on the subsystems slice: the scheduler
    # imports the reconcile entry point, which reads that slice, so parking
    # it there would close an import cycle.
    from synthorg.api.subsystems.resync import (  # noqa: PLC0415
        SubsystemResyncScheduler,
    )

    resync_scheduler = SubsystemResyncScheduler(
        app_state,
        interval_seconds=effective_config.api.subsystem_resync_interval_seconds,
    )
    startup = [*startup, resync_scheduler.start]

    async def _start_construction_dispatcher() -> None:
        # Bring up the construction-phase dispatcher's HTTP-bearing sinks under
        # their lifecycle locks, but ONLY if it is still the live dispatcher.
        # The bridge-config startup step (``_apply_notification_dispatcher_config``)
        # may have already rebuilt + started a replacement with DB-resolved
        # timeouts and swapped it onto the slice, leaving this one orphaned and
        # unstarted; starting an orphan would open sinks nothing routes through
        # and that the on-shutdown runner (which closes the LIVE slice
        # dispatcher) never tears down.
        live = app_state.slice(NotificationsStateSlice).dispatcher
        if live is notification_dispatcher:
            await notification_dispatcher.start()

    startup = [*startup, _start_construction_dispatcher]

    async def _resolve_runtime_security_settings() -> None:
        await resolve_runtime_security_settings(app_state)

    startup = [*startup, _resolve_runtime_security_settings]

    # Feature-owned service hooks: a single runner started LAST (after every
    # feature slice is composed and wired, so a hook can reference its own
    # feature's services) and stopped FIRST on shutdown (reverse order, before
    # the core teardown disconnects persistence / the message bus the hooks
    # depend on). One runner instance spans both phases so ``stop_all`` tears
    # down exactly the hooks ``start_all`` started. ``build_feature_lifecycle_runner``
    # is the ``lifecycle_hooks`` analogue of ``collect_route_handlers``
    # (controllers), ``build_handler_map`` (MCP), and ``run_construction_wiring``
    # (slices): every manifest slot is now reachable from the composition root.
    feature_lifecycle_runner = build_feature_lifecycle_runner()
    startup = [*startup, feature_lifecycle_runner.start_all]
    shutdown = [feature_lifecycle_runner.stop_all, *shutdown]

    # Ahead of the feature runner, so nothing is left that could wire a
    # subsystem back up. A sweep firing between the two teardowns would
    # reactivate against feature hooks that have already stopped.
    shutdown = [resync_scheduler.stop, *shutdown]

    return startup, shutdown
