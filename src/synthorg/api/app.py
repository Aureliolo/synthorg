"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from litestar import Litestar, Router
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin

from synthorg import __version__
from synthorg.api.app_builders import (
    _bootstrap_app_logging,
    _build_configured_autonomy_change_strategy,
    _build_configured_trust_service,
    _build_performance_tracker,
    _build_telemetry_collector,
)
from synthorg.api.app_helpers import (
    _make_expire_callback,
    _make_meeting_publisher,
    _resolve_artifact_dir_env,
)
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.controller_helpers import require_password_changed
from synthorg.api.auth.service import AuthService
from synthorg.api.auto_wire import (
    auto_wire_meetings,
    auto_wire_phase1,
)
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import (
    create_channels_plugin,
)
from synthorg.api.cursor import CursorSecret
from synthorg.api.cursor_config import CursorConfig
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.feature_composition import (
    collect_route_handlers,
    compose_feature_slices,
)
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.api.lifecycle_helpers.feature_wiring import wire_features_on_startup
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.lifecycle_helpers.startup_steps import (
    install_runtime_services,
    resolve_runtime_security_settings,
)
from synthorg.api.lifecycle_helpers.toolsmith_wiring import wire_toolsmith
from synthorg.api.middleware import security_headers_hook
from synthorg.api.middleware_factory import _build_middleware
from synthorg.api.rate_limits import (
    build_inflight_store,
    build_sliding_window_store,
)
from synthorg.api.rate_limits._subject import parse_trusted_networks
from synthorg.api.rate_limits.inflight_protocol import InflightStore
from synthorg.api.rate_limits.protocol import SlidingWindowStore
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.backup.factory import build_backup_service
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
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,
)
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.core.clock import SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.service import TrainingService
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.persistence.artifact_storage import (
    ArtifactStorageBackend,
)
from synthorg.persistence.config_factory import (
    build_filesystem_artifact_storage,
    build_postgres_persistence_config_from_url,
    build_sqlite_persistence_config,
    normalize_ssl_mode_value,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.security.timeout.policies import WaitForeverPolicy
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.security.timeout.timeout_checker import TimeoutChecker
from synthorg.security.trust.service import TrustService
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    parse_bool,
    parse_float,
    parse_str_tuple_json,
    resolve_init_int,
)
from synthorg.tools.invocation_tracker import ToolInvocationTracker

if TYPE_CHECKING:
    from litestar.channels import ChannelsPlugin

    from synthorg.client.simulation_state import ClientSimulationState
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


# Default approval-timeout interval mirrors the registry default for
# ``security.timeout_check_interval_seconds`` defined in
# ``src/synthorg/settings/definitions/security.py``. Held here as a
# constant so the bootstrap and the registry definition cannot drift;
# future reads from ConfigResolver still override at runtime via the
# scheduler's ``reschedule()`` (called from a settings subscriber).
# Update both sites together if the default ever changes; otherwise a
# bootstrap value will silently disagree with operator-editable
# overrides resolved through ``ConfigResolver``.
_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS: Final[float] = 60.0


def _resolve_rate_limiter_enabled() -> bool:
    """Resolve ``api.rate_limiter_enabled`` at app construction time.

    Cat-2 (``read_only_post_init=True``): env > default. The
    ``SettingsService`` rejects runtime mutation, so the value baked
    here lives for the process lifetime.
    """
    resolved = resolve_init_value(
        SettingNamespace.API,
        "rate_limiter_enabled",
        parse=parse_bool,
    )
    return bool(resolved.value)


def _resolve_api_str_tuple(key: str) -> tuple[str, ...]:
    """Resolve a JSON-tuple-typed api.* setting at boot.

    When the parsed value is not a tuple (e.g. invalid JSON returns None
    from the parser), the resolver applies the registered default, which
    is always a valid tuple, so this function always returns a tuple.
    """
    resolved = resolve_init_value(
        SettingNamespace.API,
        key,
        parse=parse_str_tuple_json,
    )
    if isinstance(resolved.value, tuple):
        return resolved.value
    return ()


def _resolve_api_int(key: str) -> int:
    """Resolve an integer-typed api.* setting at boot.

    Non-integer env values fall through to the registered default rather
    than raising at app construction time.
    """
    return resolve_init_int(SettingNamespace.API, key)


def _resolve_api_str(key: str) -> str:
    """Resolve a string-typed api.* setting at boot."""
    resolved = resolve_init_value(SettingNamespace.API, key)
    return str(resolved.value)


def _resolve_budget_int(key: str) -> int:
    """Resolve an integer-typed budget.* setting at boot.

    Cat-2 boot knob: the store is constructed before the
    ``SettingsService`` connects, so the value is sourced env >
    registered default via the bootstrap resolver (a runtime change
    requires a restart -- the consumer is a fixed-length ring buffer).
    """
    return resolve_init_int(SettingNamespace.BUDGET, key)


def _build_default_approval_timeout_scheduler(
    *,
    approval_store: ApprovalStoreProtocol,
) -> ApprovalTimeoutScheduler:
    """Construct an :class:`ApprovalTimeoutScheduler` with safe defaults.

    Uses :class:`WaitForeverPolicy` so the scheduler runs the periodic
    scan and emits TIMEOUT_WAITING events but never auto-decides
    pending approvals. Operators wire a real policy via the
    ``security.timeout_*`` settings; the settings subscriber on
    ``security.timeout_check_interval_seconds`` invokes
    ``scheduler.reschedule()`` so the cadence stays operator-tunable
    without restart.
    """
    timeout_checker = TimeoutChecker(policy=WaitForeverPolicy())
    return ApprovalTimeoutScheduler(
        approval_store=approval_store,
        timeout_checker=timeout_checker,
        interval_seconds=_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS,
    )


# Two-step init: construction bakes immutable middleware / CORS / routes from
# RootConfig; on_startup wires SettingsService + ConfigResolver for
# runtime-editable settings.  Litestar rate-limit middleware reads config at
# construction; runtime DB changes only affect code calling get_api_config().


def create_app(  # noqa: PLR0913
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    auth_service: AuthService | None = None,
    task_engine: TaskEngine | None = None,
    coordinator: MultiAgentCoordinator | None = None,
    work_pipeline: WorkPipeline | None = None,
    intake_entry_adapter: WorkEntryAdapter[Any] | None = None,
    task_board_entry_adapter: TaskBoardEntryAdapter | None = None,
    agent_registry: AgentRegistryService | None = None,
    meeting_orchestrator: MeetingOrchestrator | None = None,
    meeting_scheduler: MeetingScheduler | None = None,
    performance_tracker: PerformanceTracker | None = None,
    settings_service: SettingsService | None = None,
    provider_registry: ProviderRegistry | None = None,
    provider_health_tracker: ProviderHealthTracker | None = None,
    tool_invocation_tracker: ToolInvocationTracker | None = None,
    delegation_record_store: DelegationRecordStore | None = None,
    artifact_storage: ArtifactStorageBackend | None = None,
    audit_log: AuditLog | None = None,
    trust_service: TrustService | None = None,
    coordination_metrics_store: CoordinationMetricsStore | None = None,
    training_service: TrainingService | None = None,
    event_stream_hub: EventStreamHub | None = None,
    interrupt_store: InterruptStore | None = None,
    client_simulation_state: ClientSimulationState | None = None,
    _skip_lifecycle_shutdown: bool = False,
) -> Litestar:
    """Create and configure the Litestar application.

    All parameters are optional for testing -- provide fakes via
    keyword arguments.  Services not explicitly provided are
    auto-wired from config and environment variables.

    Args:
        config: Root company configuration.
        persistence: Persistence backend.
        message_bus: Internal message bus.
        cost_tracker: Cost tracking service.
        approval_store: Approval queue store.
        auth_service: Pre-built auth service (for testing).
        task_engine: Centralized task state engine.
        coordinator: Multi-agent coordinator.
        work_pipeline: Work pipeline spine (injected double wins over
            the boot-autowired one).
        intake_entry_adapter: Real work-entry adapter (injected double
            wins over the boot-autowired one).
        task_board_entry_adapter: Real task-board work-entry adapter
            (injected double wins over the boot-autowired one).
        agent_registry: Agent registry service.
        meeting_orchestrator: Meeting orchestrator.
        meeting_scheduler: Meeting scheduler.
        performance_tracker: Performance tracking service.
        settings_service: Settings service for runtime config.
        provider_registry: Provider registry.
        provider_health_tracker: Provider health tracking service.
        tool_invocation_tracker: Tool invocation tracking service.
        delegation_record_store: Delegation record store.
        artifact_storage: Artifact storage backend.
        audit_log: Pre-built audit log (auto-wired if None).
        trust_service: Pre-built trust service.
        coordination_metrics_store: Pre-built metrics store
            (auto-wired if None).
        training_service: Pre-built training service (auto-wired
            in startup if None and dependencies are available).
        event_stream_hub: Pre-built event stream hub (auto-created
            if None).
        interrupt_store: Pre-built interrupt store (auto-created
            if None).
        client_simulation_state: Pre-built client simulation state.
            Wired before the optional-controller predicate check so
            the Simulation / Request controllers register correctly
            on a test app boot.
        _skip_lifecycle_shutdown: Test-only flag.  When ``True``, the
            Litestar app is built with an empty ``on_shutdown`` list so
            the lifespan exit is a no-op.  Used by the session-scoped
            test fixture in ``tests/unit/api/conftest.py`` to reuse the
            same app across tests without tearing down the task engine,
            message bus, and persistence between each one.  Never use
            in production: shutdown hooks perform critical cleanup
            (task-engine drain, persistence disconnect, health prober
            stop, etc.).

    Returns:
        Configured Litestar application.
    """
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

    # Resolve runtime paths for backup service wiring.
    resolved_db_path: Path | None = None
    resolved_config_path_str = (os.environ.get("SYNTHORG_CONFIG_PATH") or "").strip()
    resolved_config_path: Path | None = (
        Path(resolved_config_path_str) if resolved_config_path_str else None
    )

    # Read persistence env vars unconditionally so downstream code
    # (e.g. the secret-backend gate below) can still observe which
    # environment choice won, even when ``persistence`` was injected
    # by the caller rather than auto-wired here.
    db_url = (os.environ.get("SYNTHORG_DATABASE_URL") or "").strip()
    db_path = (os.environ.get("SYNTHORG_DB_PATH") or "").strip()

    # Auto-wire persistence from CLI-provided env vars. The CLI compose
    # template sets ONE of these per init choice:
    #   * SYNTHORG_DATABASE_URL=postgresql://user:pass@host:port/db   (postgres)
    #   * SYNTHORG_DB_PATH=/data/synthorg.db                          (sqlite)
    # Postgres takes precedence so a half-converted state (both env
    # vars present) does not silently fall back to SQLite. The startup
    # lifecycle handles connect() + migrate() + auth service creation.
    if persistence is None:
        if db_url:
            try:
                pg_persistence_config = build_postgres_persistence_config_from_url(
                    db_url,
                    ssl_mode_override=normalize_ssl_mode_value(
                        os.environ.get("SYNTHORG_POSTGRES_SSL_MODE"),
                    ),
                )
                persistence = create_backend(pg_persistence_config)
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
                    note="Postgres persistence creation failed",
                )
                raise
            assert pg_persistence_config.postgres is not None  # noqa: S101
            logger.info(
                API_APP_STARTUP,
                note="Auto-wired Postgres persistence from SYNTHORG_DATABASE_URL",
                host=pg_persistence_config.postgres.host,
                database=pg_persistence_config.postgres.database,
            )
            # Postgres has no on-disk artifact directory tied to the DB
            # path, so default artifact storage to /data (the standard
            # data volume in the CLI compose template) when not set.
            if artifact_storage is None:
                artifact_dir_str = _resolve_artifact_dir_env()
                artifact_storage = build_filesystem_artifact_storage(
                    data_dir=Path(artifact_dir_str),
                )
                logger.info(
                    API_APP_STARTUP,
                    note="Auto-wired filesystem artifact storage (postgres mode)",
                    data_dir=artifact_dir_str,
                )
        elif db_path:
            resolved_db_path = Path(db_path)
            try:
                persistence = create_backend(
                    build_sqlite_persistence_config(path=db_path),
                )
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
                    note="Failed to create persistence backend from env",
                )
                raise
            logger.info(
                API_APP_STARTUP,
                note="Auto-wired SQLite persistence from SYNTHORG_DB_PATH",
                db_name=Path(db_path).name,
            )
            # Auto-wire artifact storage from the same data directory.
            if artifact_storage is None:
                artifact_storage = build_filesystem_artifact_storage(
                    data_dir=resolved_db_path.parent,
                )
                logger.info(
                    API_APP_STARTUP,
                    note="Auto-wired filesystem artifact storage",
                )

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
    distributed_task_queue = phase1.distributed_task_queue
    distributed_dispatcher = phase1.distributed_dispatcher
    distributed_backend_services = phase1.distributed_backend_services

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
    oauth_token_manager = integrations.oauth_token_manager
    health_prober_service = integrations.health_prober_service
    tunnel_provider = integrations.tunnel_provider
    webhook_event_bridge = integrations.webhook_event_bridge
    mcp_catalog_service = integrations.mcp_catalog_service
    mcp_installations_repo = integrations.mcp_installations_repo

    # Auto-wire control-plane services when not injected.
    if audit_log is None:
        audit_log = AuditLog()
    if coordination_metrics_store is None:
        coordination_metrics_store = CoordinationMetricsStore(
            max_entries=_resolve_budget_int("coordination_metrics_max_entries"),
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
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.communication.state import (  # noqa: PLC0415
        CommunicationStateSlice,
    )
    from synthorg.coordination.state import (  # noqa: PLC0415
        CoordinationStateSlice,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        WorkspaceStateSlice,
    )
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.integrations.state import (  # noqa: PLC0415
        IntegrationsStateSlice,
    )
    from synthorg.notifications.state import (  # noqa: PLC0415
        NotificationsStateSlice,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
    from synthorg.security.state import SecurityStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415
    from synthorg.tools.state import ToolsStateSlice  # noqa: PLC0415

    # ``model_construct`` skips validation so the slices accept the same
    # already-constructed services the legacy slots held (and the test
    # doubles injected via ``create_app``), matching the no-revalidation
    # behaviour of the old attribute bag and the lazy-wire ``model_copy``
    # shims.
    app_state.swap_slice(
        SecurityStateSlice.model_construct(
            audit_log=audit_log,
            trust_service=trust_service,
            autonomy_change_strategy=autonomy_change_strategy,
        )
    )
    app_state.swap_slice(
        ToolsStateSlice.model_construct(invocation_tracker=tool_invocation_tracker)
    )
    app_state.swap_slice(
        CoordinationStateSlice.model_construct(metrics_store=coordination_metrics_store)
    )
    app_state.swap_slice(
        ApprovalStateSlice.model_construct(store=effective_approval_store)
    )
    app_state.swap_slice(PersistenceStateSlice.model_construct(backend=persistence))
    app_state.swap_slice(
        ProvidersStateSlice.model_construct(
            registry=provider_registry,
            health_tracker=provider_health_tracker,
        )
    )
    app_state.swap_slice(
        HrStateSlice.model_construct(
            agent_registry=agent_registry,
            performance_tracker=performance_tracker,
            training_service=training_service,
        )
    )
    app_state.swap_slice(
        CommunicationStateSlice.model_construct(
            message_bus=message_bus,
            meeting_orchestrator=meeting_orchestrator,
            meeting_scheduler=meeting_scheduler,
            event_stream_hub=event_stream_hub,
            interrupt_store=interrupt_store,
            delegation_record_store=delegation_record_store,
        )
    )
    app_state.swap_slice(BudgetStateSlice.model_construct(cost_tracker=cost_tracker))
    app_state.swap_slice(
        EngineStateSlice.model_construct(
            task_engine=task_engine,
            work_pipeline=work_pipeline,
            ceremony_scheduler=ceremony_scheduler,
            intake_entry_adapter=intake_entry_adapter,
            task_board_entry_adapter=task_board_entry_adapter,
        )
    )
    app_state.swap_slice(
        IntegrationsStateSlice.model_construct(
            connection_catalog=connection_catalog,
            oauth_token_manager=oauth_token_manager,
            health_prober_service=health_prober_service,
            tunnel_provider=tunnel_provider,
            webhook_event_bridge=webhook_event_bridge,
            mcp_catalog_service=mcp_catalog_service,
            mcp_installations_repo=mcp_installations_repo,
        )
    )
    app_state.swap_slice(
        SettingsStateSlice.model_construct(settings_service=settings_service)
    )
    app_state.swap_slice(
        WorkspaceStateSlice.model_construct(artifact_storage=artifact_storage)
    )
    app_state.swap_slice(
        NotificationsStateSlice.model_construct(dispatcher=notification_dispatcher)
    )
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    app_state.swap_slice(
        RuntimeStateSlice.model_construct(
            coordinator=coordinator,
            distributed_task_queue=distributed_task_queue,
            distributed_backend_services=distributed_backend_services,
        )
    )
    if distributed_dispatcher is not None:
        # Late-bind the live bridge-config provider now that AppState
        # exists (the dispatcher is built in auto_wire_phase1 before
        # AppState). Each publish then reads the current snapshot, so
        # an operator hot-reload of a workers.dispatcher_publish_*
        # setting takes effect without restarting the dispatcher.
        distributed_dispatcher.set_workers_bridge_provider(
            lambda: app_state.workers_bridge_config,
        )

    # Opaque pagination cursor HMAC secret.  Loaded from the
    # ``SYNTHORG_PAGINATION_CURSOR_SECRET`` env var; rolling with a
    # random per-process key silently invalidates every client cursor
    # on every restart, which is a correctness defect, not a warning.
    # We refuse to boot unconditionally -- dev, pre-release, and prod
    # share the same posture so this latent failure can never hide
    # behind a "looks fine in dev" code path.
    cursor_secret = CursorSecret.from_config(CursorConfig.from_env())
    from synthorg.api.api_core_state import ApiCoreStateSlice  # noqa: PLC0415
    from synthorg.api.auth.presence import UserPresence  # noqa: PLC0415
    from synthorg.api.auth.ticket_store import WsTicketStore  # noqa: PLC0415

    app_state.swap_slice(
        ApiCoreStateSlice.model_construct(
            cursor_secret=cursor_secret,
            auth_service=auth_service,
            ticket_store=WsTicketStore(),
            user_presence=UserPresence(),
        )
    )
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
    approval_timeout_scheduler = _build_default_approval_timeout_scheduler(
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
    rate_limiter_enabled = _resolve_rate_limiter_enabled()
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

    # ── A2A gateway auto-wire ─────────────────────────────────────
    # The a2a controllers are discovery-registered from the a2a feature
    # manifest: the well-known Agent Card controller at the application
    # root and the JSON-RPC gateway under the API prefix, each gated by a
    # predicate reading the committed a2a state slice. This block builds
    # the a2a collaborators and commits them to the slice on full success;
    # a partial failure leaves the slice empty so the predicates keep both
    # controllers unmounted (the historic build-all-then-commit guard).
    if effective_config.a2a.enabled:
        a2a_card_builder = None
        a2a_peer_registry = None
        a2a_client_obj = None
        try:
            from synthorg.a2a.agent_card import (  # noqa: PLC0415
                AgentCardBuilder,
            )
            from synthorg.a2a.models import A2AAuthSchemeInfo  # noqa: PLC0415

            auth_schemes = (
                A2AAuthSchemeInfo(
                    scheme=str(
                        effective_config.a2a.auth.inbound_scheme,
                    ),
                ),
            )
            a2a_card_builder = AgentCardBuilder(
                default_auth_schemes=auth_schemes,
            )

            # Outbound client + JSON-RPC gateway need the connection
            # catalog and integrations enabled.
            if effective_config.integrations.enabled and connection_catalog is not None:
                import httpx  # noqa: PLC0415

                from synthorg.a2a.client import A2AClient  # noqa: PLC0415
                from synthorg.a2a.peer_registry import (  # noqa: PLC0415
                    PeerRegistry,
                )

                a2a_peer_registry = PeerRegistry()
                a2a_client_timeout = float(
                    resolve_init_value(
                        SettingNamespace.A2A,
                        "client_timeout_seconds",
                        parse=parse_float,
                    ).value
                )
                a2a_http_client = httpx.AsyncClient(timeout=a2a_client_timeout)
                from synthorg.tools.network_validator import (  # noqa: PLC0415
                    NetworkPolicy,
                )

                a2a_network_policy = NetworkPolicy()
                a2a_client_obj = A2AClient(
                    connection_catalog,
                    network_validator=a2a_network_policy,
                    http_client=a2a_http_client,
                    timeout_seconds=a2a_client_timeout,
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                note="A2A gateway auto-wire failed (non-fatal)",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            # Commit only on full success. Partial failures land in
            # the except branch above with the slice still empty.
            from synthorg.a2a.state import A2aStateSlice  # noqa: PLC0415

            app_state.swap_slice(
                A2aStateSlice(
                    card_builder=a2a_card_builder,
                    client=a2a_client_obj,
                    peer_registry=a2a_peer_registry,
                )
            )
            logger.info(
                API_SERVICE_AUTO_WIRED,
                service="a2a_gateway",
            )

    # Client-simulation runtime. An explicit kwarg always wins (test
    # doubles / bespoke wiring). Otherwise, when a TaskEngine is
    # present, build the live runtime (real IntakeEngine + review
    # pipeline) so ``has_simulation_runtime`` is true and the
    # ``/simulations`` + ``/requests`` controllers register; the
    # default ``direct`` intake strategy makes no LLM calls and works
    # for an empty company. With no TaskEngine the intake engine has
    # nothing to create tasks against, so fall back to a fresh empty
    # ``ClientSimulationState()`` -- the always-registered
    # ``ClientController`` still serves an empty ``/clients`` list
    # instead of 503ing on every dashboard poll. This mirrors the
    # ``review_gate_service`` "build it whenever task_engine exists"
    # gate above.
    if client_simulation_state is None:
        if task_engine is not None:
            from synthorg.client.runtime_builder import (  # noqa: PLC0415
                build_client_simulation_runtime,
            )

            client_simulation_state = build_client_simulation_runtime(app_state)
        else:
            from synthorg.client.simulation_state import (  # noqa: PLC0415
                ClientSimulationState as _ClientSimulationState,
            )

            client_simulation_state = _ClientSimulationState()
    from synthorg.client.state import ClientStateSlice  # noqa: PLC0415

    app_state.wire(ClientStateSlice, simulation_state=client_simulation_state)

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

    # Review gate service -- transitions tasks from IN_REVIEW on approval.
    # Needs ``task_engine`` for self-review enforcement (preflight) and
    # state transitions; ``persistence`` is OPTIONAL and only used for
    # the auditable decisions drop-box.  Construct the service whenever
    # ``task_engine`` exists so the fail-fast self-review / missing-task
    # preflight still runs in task-engine-only deployments; decision
    # recording gracefully degrades to a WARNING-level no-op when
    # persistence is absent.
    if task_engine is not None:
        review_gate_service = ReviewGateService(
            task_engine=task_engine,
            persistence=persistence,
        )
        from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

        app_state.swap_slice(
            app_state.slice(ApprovalStateSlice).model_copy(
                update={"review_gate": review_gate_service}
            )
        )

    # ``approval_timeout_scheduler`` is built above (alongside the
    # backup service and bridge); the lifecycle owns starting it.
    # ``_apply_security_timeout_interval`` in ``lifecycle_helpers.py``
    # resolves the operator-tuned interval from ``ConfigResolver`` after
    # persistence connects and calls ``scheduler.reschedule(...)`` so the
    # configured cadence takes effect on the next loop tick. Default
    # policy is ``WaitForeverPolicy``: the scheduler runs but never
    # auto-decides. Operators swap in DenyOnTimeout / Tiered /
    # EscalationChain via the security.* settings at runtime.

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
        should_auto_wire_settings=_should_auto_wire,
        effective_config=effective_config,
    )

    _runtime_services_installed = False

    async def _install_runtime_services() -> None:
        # Installs the worker execution service AND the multi-agent
        # coordinator behind the single provider-present switch, both
        # sharing one boot AgentEngine. Appended first (runs immediately
        # after the core startup hooks that connect persistence and
        # wire SettingsService / ConfigResolver), and before any other
        # appended hook, so the once-only ``set_worker_execution_service``
        # / ``set_coordinator`` cannot lose a race with the
        # worker-service property's lazy lifecycle-only default. With no
        # provider this installs the empty-company backstop and no
        # coordinator (``/coordinate`` honestly 503s); a provider added
        # later swaps both in via ``post_setup_reinit`` (no restart). The
        # closure flag keeps the one-shot ``set_`` calls idempotent
        # across a lifespan re-entry (shared-app test fixtures),
        # mirroring ``_wire_chief_of_staff_chat``.
        nonlocal _runtime_services_installed
        if _runtime_services_installed:
            return
        await install_runtime_services(
            app_state,
            connection_catalog=connection_catalog,
        )
        _runtime_services_installed = True

    _brownfield_intake_installed = False

    async def _wire_brownfield_intake() -> None:
        # Brownfield codebase intake (the "merger/acquisition" entry mode).
        # Runs AFTER _wire_knowledge_engine so the import service can index
        # the codebase into the knowledge store. Best-effort + idempotent:
        # a missing collaborator (no persistence / workspace / knowledge)
        # leaves the /brownfield controller to 503 rather than poisoning
        # startup.
        nonlocal _brownfield_intake_installed
        if _brownfield_intake_installed:
            return
        from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
            wire_real_brownfield_entry,
        )

        try:
            await wire_real_brownfield_entry(app_state)
            _brownfield_intake_installed = True
        except Exception as exc:
            reraise_critical(exc)
            logger.info(
                API_APP_STARTUP,
                service="brownfield_intake",
                note="brownfield intake wiring unavailable; skipped",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _compose_feature_slices() -> None:
        compose_feature_slices(app_state)

    # ``_compose_feature_slices`` runs FIRST so every feature's empty state
    # slice exists before any wiring hook (including the persistence-phase
    # ``_safe_startup`` hooks) composes/swaps a populated slice.
    async def _wire_features() -> None:
        await wire_features_on_startup(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            effective_approval_store=effective_approval_store,
        )

    startup = [
        _compose_feature_slices,
        *startup,
        _install_runtime_services,
        _wire_features,
        _wire_brownfield_intake,
    ]

    # Project telemetry: build collector (reads SYNTHORG_TELEMETRY_ENABLED env for
    # opt-in, defaults to disabled). Attach to app_state so the health
    # endpoint can report the state, and hook start()/shutdown() into the
    # Litestar lifespan. Telemetry is SynthOrg-owned and silent on
    # failure: a broken reporter falls back to noop and never affects
    # the app.
    #
    # Shutdown is appended (runs LAST), not prepended: critical
    # infrastructure (task engine drain, persistence disconnect, bus
    # stop) must complete first so the session-summary event emitted
    # by ``telemetry_collector.shutdown`` reflects final state, and so
    # a hanging Logfire flush never blocks cleanup of load-bearing
    # resources.
    telemetry_collector = _build_telemetry_collector(effective_config.telemetry)
    from synthorg.telemetry.state import TelemetryStateSlice  # noqa: PLC0415

    app_state.swap_slice(TelemetryStateSlice(collector=telemetry_collector))
    startup = [*startup, telemetry_collector.start]
    shutdown = [*shutdown, telemetry_collector.shutdown]

    # Automated report service: wired from the cost tracker + budget config
    # so the ``POST /api/v1/reports/generate`` endpoint can serve the
    # documented inputs instead of returning 503 unconfigured. Risk and
    # performance trackers are optional; the service degrades to empty
    # per-tracker reports when either is absent (see
    # ``AutomatedReportService.generate_*_report`` for the None-tolerant
    # paths). When ``cost_tracker`` is itself absent (degenerate test
    # configurations) we skip the wire and the controller falls back to
    # 503 ServiceUnavailableError -- which is the honest status code for
    # "feature unavailable", not the AttributeError it used to surface.
    if cost_tracker is not None:
        from synthorg.budget.automated_reports import (  # noqa: PLC0415
            AutomatedReportService,
        )
        from synthorg.budget.reports import ReportGenerator  # noqa: PLC0415

        report_generator = ReportGenerator(
            cost_tracker=cost_tracker,
            budget_config=effective_config.budget,
        )
        report_service = AutomatedReportService(
            report_generator=report_generator,
            cost_tracker=cost_tracker,
            risk_tracker=None,
            performance_tracker=performance_tracker,
        )
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

        app_state.wire(BudgetStateSlice, report_service=report_service)

    async def _wire_toolsmith() -> None:
        await wire_toolsmith(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            approval_store=effective_approval_store,
            cost_tracker=cost_tracker,
        )

    startup = [*startup, _wire_toolsmith]

    # Bring up the notification dispatcher's HTTP-bearing sinks
    # (slack/ntfy ``httpx.AsyncClient``) lazily under their lifecycle
    # locks. Stateless sinks (console/email) implement no-op
    # start()/close() so the fan-out treats every adapter uniformly.
    # Shutdown registration lives in ``lifecycle_builder._safe_shutdown``
    # via ``notification_dispatcher.aclose`` so audit-style shutdown
    # notifications can fire during service teardown before sink
    # close() runs.
    startup = [*startup, notification_dispatcher.start]

    async def _resolve_runtime_security_settings() -> None:
        await resolve_runtime_security_settings(app_state)

    startup = [*startup, _resolve_runtime_security_settings]

    if _skip_lifecycle_shutdown:
        shutdown = []

    # Per-operation rate limiter.  Layered on top of the global
    # two-tier limiter; read from app state by ``per_op_rate_limit``
    # guards.  The store is built unconditionally so that operators who
    # toggle ``api.per_op_rate_limit_enabled`` at runtime (the setting
    # is marked runtime-editable) do not land on a wired-but-uncapped
    # request path; the config's ``enabled`` flag short-circuits the
    # guard when disabled.  Store construction is cheap (empty dicts +
    # per-key locks materialise lazily on first acquire).
    per_op_rate_limit_store: SlidingWindowStore = build_sliding_window_store(
        api_config.per_op_rate_limit,
    )
    app_state.set_per_op_rate_limit_config(api_config.per_op_rate_limit)
    # Honour ``_skip_lifecycle_shutdown`` so tests that share an
    # app across multiple lifespans do not tear down the store
    # (and its background GC) on the first teardown.
    if not _skip_lifecycle_shutdown:
        shutdown = [*shutdown, per_op_rate_limit_store.close]

    # Per-operation inflight-concurrency limiter.
    # Layered on top of the sliding-window per-op limiter; caps
    # simultaneous long-running requests per (operation, subject).
    # Enforced by ``PerOpConcurrencyMiddleware`` registered in the
    # middleware stack.  Built unconditionally (same rationale as the
    # sliding-window store): runtime toggling of
    # ``api.per_op_concurrency_enabled`` must not encounter a missing
    # store.  The middleware short-circuits when
    # ``config.enabled`` is False without ever touching the store.
    per_op_inflight_store: InflightStore = build_inflight_store(
        api_config.per_op_concurrency,
    )
    app_state.set_per_op_concurrency_config(api_config.per_op_concurrency)
    if not _skip_lifecycle_shutdown:
        shutdown = [*shutdown, per_op_inflight_store.close]

    _trusted_proxies = _resolve_api_str_tuple("trusted_proxies")

    return Litestar(
        route_handlers=[api_router, *root_handlers],
        # Disable Litestar's built-in logging config to preserve the
        # structlog multi-file-sink pipeline set up by
        # _bootstrap_app_logging() above.  Without this, Litestar calls
        # dictConfig() at startup which triggers _clearExistingHandlers
        # and replaces structlog's file sinks with a stdlib
        # queue_listener, causing all runtime logs to go only to Docker
        # stdout.
        logging_config=None,
        state=State(
            {
                "app_state": app_state,
                "per_op_rate_limit_store": per_op_rate_limit_store,
                "per_op_rate_limit_config": api_config.per_op_rate_limit,
                # Inflight-concurrency state used by
                # ``PerOpConcurrencyMiddleware``; mirrors the
                # sliding-window store's wiring.
                "per_op_inflight_store": per_op_inflight_store,
                "per_op_inflight_config": api_config.per_op_concurrency,
                # Mirrors the global limiter's trusted-proxy set so the
                # per-op guard extracts the same "real" client IP behind
                # reverse proxies instead of bucketing all traffic by
                # the proxy's IP.  The raw frozenset is kept for
                # diagnostic reads; the parsed tuple beside it is what
                # the guards consult per-request.
                "per_op_trusted_proxies": frozenset(_trusted_proxies),
                "per_op_trusted_networks": parse_trusted_networks(
                    frozenset(_trusted_proxies),
                ),
            },
        ),
        cors_config=CORSConfig(
            allow_origins=list(_resolve_api_str_tuple("cors_allowed_origins")),
            allow_methods=list(api_config.cors.allow_methods),  # type: ignore[arg-type]
            allow_headers=list(api_config.cors.allow_headers),
            allow_credentials=api_config.cors.allow_credentials,
        ),
        compression_config=CompressionConfig(
            backend="brotli",
            minimum_size=_resolve_api_int("compression_minimum_size_bytes"),
        ),
        # Must be >= artifact API max payload (50 MB) so endpoint-level
        # validation can enforce exact storage limits.
        request_max_body_size=_resolve_api_int("request_max_body_size_bytes"),
        before_send=[security_headers_hook],
        middleware=middleware,
        plugins=plugins,
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        openapi_config=OpenAPIConfig(
            title="SynthOrg API",
            version=__version__,
            path="/docs",
            render_plugins=[
                ScalarRenderPlugin(path="/api"),
            ],
        ),
        on_startup=startup,
        on_shutdown=shutdown,
    )
