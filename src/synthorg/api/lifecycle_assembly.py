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
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.feature_composition import compose_feature_slices
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.api.lifecycle_helpers.feature_lifecycle import (
    build_feature_lifecycle_runner,
)
from synthorg.api.lifecycle_helpers.feature_wiring import wire_features_on_startup
from synthorg.api.lifecycle_helpers.startup_steps import (
    install_runtime_services,
    resolve_runtime_security_settings,
    wire_brownfield_intake,
)
from synthorg.api.lifecycle_helpers.toolsmith_wiring import wire_toolsmith
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.backup.service import BackupService
from synthorg.budget.automated_reports import AutomatedReportService
from synthorg.budget.reports import ReportGenerator
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.telemetry.state import TelemetryStateSlice

type LifespanHooks = list[Callable[[], Awaitable[None]]]


def assemble_lifespan_hooks(  # noqa: PLR0913
    app_state: AppState,
    *,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    should_auto_wire_settings: bool,
    effective_config: RootConfig,
    connection_catalog: ConnectionCatalog | None,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTracker | None,
    approval_store: ApprovalStoreProtocol,
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
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval-timeout checker.
        should_auto_wire_settings: When ``True``, the on-startup auto-wiring
            creates ``SettingsService`` after persistence connects.
        effective_config: Root configuration.
        connection_catalog: Integration connection catalog (runtime services).
        provider_registry: Provider registry (feature wiring + toolsmith).
        cost_tracker: Cost tracker (feature wiring + report service).
        approval_store: Approval queue store (feature wiring + toolsmith).
        performance_tracker: Performance tracker (report service).
        notification_dispatcher: Notification dispatcher (HTTP sink start-up).

    Returns:
        A tuple of (on_startup, on_shutdown) callback lists in run order.
    """
    startup, shutdown = _build_lifecycle(
        persistence,
        message_bus,
        bridge,
        settings_dispatcher,
        task_engine,
        meeting_scheduler,
        backup_service,
        approval_timeout_scheduler,
        app_state,
        should_auto_wire_settings=should_auto_wire_settings,
        effective_config=effective_config,
    )

    runtime_services_installed = False

    async def _install_runtime_services() -> None:
        nonlocal runtime_services_installed
        if runtime_services_installed:
            return
        await install_runtime_services(
            app_state,
            connection_catalog=connection_catalog,
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

    async def _wire_evolution_outcomes() -> None:
        from synthorg.api.lifecycle_helpers.evolution_outcomes_wiring import (  # noqa: PLC0415
            wire_evolution_outcomes,
        )

        await wire_evolution_outcomes(app_state)

    async def _compose_feature_slices() -> None:
        compose_feature_slices(app_state)

    async def _wire_features() -> None:
        await wire_features_on_startup(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            effective_approval_store=approval_store,
        )

    # ``_compose_feature_slices`` runs FIRST so every feature's empty state
    # slice exists before any wiring hook (including the persistence-phase
    # ``_safe_startup`` hooks) composes/swaps a populated slice.
    startup = [
        _compose_feature_slices,
        *startup,
        # After persistence connects (core hooks above) and before runtime
        # services parse providers: migrate any embedded api_key into the
        # catalog so the resolver does not reject the stored config.
        _migrate_provider_credentials,
        # Build the durable evolution-outcome store BEFORE runtime services
        # so the engine evolution loop reads it as its outcome sink, and
        # before signals wiring so the aggregator shares the same store.
        _wire_evolution_outcomes,
        _install_runtime_services,
        _wire_features,
        _wire_brownfield_intake,
    ]

    # Project telemetry collector. Attach to app_state so the health endpoint
    # reports state; start runs in the lifespan, shutdown is appended (runs
    # LAST) so the session-summary event reflects final state after the
    # load-bearing teardown and a hanging flush never blocks cleanup.
    telemetry_collector = _build_telemetry_collector(effective_config.telemetry)
    app_state.swap_slice(TelemetryStateSlice(collector=telemetry_collector))
    startup = [*startup, telemetry_collector.start]
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

    async def _wire_toolsmith() -> None:
        await wire_toolsmith(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            approval_store=approval_store,
            cost_tracker=cost_tracker,
        )

    startup = [*startup, _wire_toolsmith]

    async def _wire_model_refresh() -> None:
        from synthorg.api.lifecycle_helpers.model_refresh_wiring import (  # noqa: PLC0415
            wire_model_refresh,
        )

        await wire_model_refresh(app_state)

    startup = [*startup, _wire_model_refresh]

    async def _wire_promotion() -> None:
        from synthorg.api.lifecycle_helpers.promotion_wiring import (  # noqa: PLC0415
            wire_promotion,
        )

        await wire_promotion(app_state, config=effective_config.promotion)

    startup = [*startup, _wire_promotion]

    async def _wire_pruning() -> None:
        from synthorg.api.lifecycle_helpers.pruning_wiring import (  # noqa: PLC0415
            wire_pruning,
        )

        await wire_pruning(app_state)

    startup = [*startup, _wire_pruning]

    # Bring up the notification dispatcher's HTTP-bearing sinks lazily under
    # their lifecycle locks. Teardown lives in the on-shutdown runner
    # (``lifecycle_runner_shutdown``) via ``notification_dispatcher.aclose``.
    startup = [*startup, notification_dispatcher.start]

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

    return startup, shutdown
