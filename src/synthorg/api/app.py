"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from litestar import Controller, Litestar, Router
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin
from pydantic import ValidationError

from synthorg import __version__
from synthorg.api.app_builders import (
    _bootstrap_app_logging,
    _build_configured_trust_service,
    _build_performance_tracker,
    _build_telemetry_collector,
    build_chief_of_staff_chat,
)
from synthorg.api.app_helpers import (
    _make_expire_callback,
    _make_meeting_publisher,
    _resolve_artifact_dir_env,
    resolve_agent_workspace_root_env,
)
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.controller_helpers import require_password_changed
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auto_wire import (
    auto_wire_meetings,
    auto_wire_phase1,
)
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import (
    create_channels_plugin,
)
from synthorg.api.controllers import BASE_CONTROLLERS, OPTIONAL_CONTROLLERS
from synthorg.api.controllers.ws import ws_handler
from synthorg.api.cursor import CursorSecret
from synthorg.api.cursor_config import CursorConfig
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.middleware import security_headers_hook, set_docs_csp_origins
from synthorg.api.middleware_factory import _build_middleware
from synthorg.api.rate_limits import (
    build_inflight_store,
    build_sliding_window_store,
)
from synthorg.api.rate_limits._subject import parse_trusted_networks
from synthorg.api.rate_limits.inflight_protocol import InflightStore  # noqa: TC001
from synthorg.api.rate_limits.protocol import SlidingWindowStore  # noqa: TC001
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol  # noqa: TC001
from synthorg.backup.factory import build_backup_service
from synthorg.budget.coordination_store import (
    CoordinationMetricsStore,
)
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
from synthorg.communication.conflict_resolution.escalation import (
    EscalationExpirationSweeper,
    PendingFuturesRegistry,
    build_decision_processor,
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,  # noqa: TC001
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,  # noqa: TC001
)
from synthorg.communication.meeting.scheduler import MeetingScheduler  # noqa: TC001
from synthorg.config.schema import RootConfig
from synthorg.core.clock import SystemClock
from synthorg.core.error_taxonomy import set_error_docs_base_url
from synthorg.engine.coordination.service import MultiAgentCoordinator  # noqa: TC001
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine  # noqa: TC001
from synthorg.hr.performance.tracker import PerformanceTracker  # noqa: TC001
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.service import TrainingService  # noqa: TC001
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.persistence.artifact_storage import (
    ArtifactStorageBackend,  # noqa: TC001
)
from synthorg.persistence.config_factory import (
    build_filesystem_artifact_storage,
    build_postgres_persistence_config_from_url,
    build_sqlite_persistence_config,
    normalize_ssl_mode_value,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001
from synthorg.providers.health import ProviderHealthTracker  # noqa: TC001
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.security.audit import AuditLog
from synthorg.security.timeout.policies import WaitForeverPolicy
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.security.timeout.timeout_checker import TimeoutChecker
from synthorg.security.trust.service import TrustService  # noqa: TC001
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
)
from synthorg.settings.mirrors import (
    parse_bool,
    parse_float,
    parse_int,
    parse_str_tuple_json,
)
from synthorg.tools.invocation_tracker import ToolInvocationTracker  # noqa: TC001

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

    Uses ``parse_int`` so a non-integer env value falls through to the
    registered default rather than raising at app construction time.
    """
    resolved = resolve_init_value(SettingNamespace.API, key, parse=parse_int)
    return int(resolved.value)


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
    resolved = resolve_init_value(SettingNamespace.BUDGET, key, parse=parse_int)
    return int(resolved.value)


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


def create_app(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    auth_service: AuthService | None = None,
    task_engine: TaskEngine | None = None,
    coordinator: MultiAgentCoordinator | None = None,
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    API_APP_STARTUP,
                    note="Postgres persistence creation failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    API_APP_STARTUP,
                    note="Failed to create persistence backend from env",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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

    # One boot clock shared between the uptime baseline and AppState so
    # ``app_state.clock`` and ``startup_time`` cannot diverge, and a
    # FakeClock injected via AppState in tests governs both.
    _boot_clock = SystemClock()
    app_state = AppState(
        clock=_boot_clock,
        config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
        cost_tracker=cost_tracker,
        approval_store=effective_approval_store,
        auth_service=auth_service,
        task_engine=task_engine,
        coordinator=coordinator,
        agent_registry=agent_registry,
        meeting_orchestrator=meeting_orchestrator,
        meeting_scheduler=meeting_scheduler,
        ceremony_scheduler=ceremony_scheduler,
        performance_tracker=performance_tracker,
        settings_service=settings_service,
        provider_registry=provider_registry,
        provider_health_tracker=provider_health_tracker,
        tool_invocation_tracker=tool_invocation_tracker,
        delegation_record_store=delegation_record_store,
        artifact_storage=artifact_storage,
        notification_dispatcher=notification_dispatcher,
        audit_log=audit_log,
        trust_service=trust_service,
        coordination_metrics_store=coordination_metrics_store,
        event_stream_hub=event_stream_hub or EventStreamHub(),
        interrupt_store=interrupt_store or InterruptStore(),
        connection_catalog=connection_catalog,
        oauth_token_manager=oauth_token_manager,
        health_prober_service=health_prober_service,
        tunnel_provider=tunnel_provider,
        webhook_event_bridge=webhook_event_bridge,
        mcp_catalog_service=mcp_catalog_service,
        mcp_installations_repo=mcp_installations_repo,
        training_service=training_service,
        startup_time=_boot_clock.monotonic(),
    )
    if distributed_task_queue is not None:
        app_state.set_distributed_task_queue(distributed_task_queue)
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
    app_state.set_cursor_secret(cursor_secret)
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

        app_state.set_message_service(
            MessageService(bus=message_bus, persistence=persistence),
        )
    if meeting_orchestrator is not None:
        from synthorg.communication.meetings.service import (  # noqa: PLC0415
            MeetingService,
        )

        app_state.set_meeting_service(
            MeetingService(orchestrator=meeting_orchestrator),
        )

    app_state.set_escalation_store(_escalation_store)
    app_state.set_escalation_processor(build_decision_processor(escalation_config))
    _escalation_registry = PendingFuturesRegistry()
    app_state.set_escalation_registry(_escalation_registry)
    app_state.set_escalation_sweeper(
        EscalationExpirationSweeper(
            _escalation_store,
            interval_seconds=escalation_config.sweeper_interval_seconds,
        ),
    )
    # Cross-instance wake-up subscriber. No-op unless the queue
    # backend is Postgres and ``cross_instance_notify`` is enabled;
    # otherwise the sweeper and per-resolver timeout cover eventual
    # consistency on their own.
    app_state.set_escalation_notify_subscriber(
        build_escalation_notify_subscriber(
            escalation_config,
            _escalation_store,
            _escalation_registry,
            reconnect_delay_seconds=escalation_config.reconnect_delay_seconds,
            config_resolver=app_state.config_resolver
            if app_state.has_config_resolver
            else None,
        ),
    )

    bridge = (
        MessageBusBridge(
            message_bus,
            channels_plugin,
            config_resolver=(
                app_state.config_resolver if app_state.has_config_resolver else None
            ),
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

    # Integration controllers add ~20 routes (~0.7s of Litestar
    # registration per create_app). Skip them entirely when the
    # integrations subsystem is disabled, so unit tests that do not
    # exercise integration endpoints pay no registration cost.
    #
    # When enabled, gate each controller by its own collaborators
    # instead of a single boolean. ``MCPCatalogController`` only
    # needs ``mcp_catalog_service``; ``WebhooksController`` needs a
    # bus; ``TunnelController`` needs ``tunnel_provider``. A single
    # global gate either under-exposes controllers that are ready
    # or over-exposes ones whose dependencies failed to auto-wire.
    integration_controllers: tuple[type[Controller], ...] = ()
    if effective_config.integrations.enabled:
        from synthorg.api.controllers.connections import (  # noqa: PLC0415
            ConnectionsController,
        )
        from synthorg.api.controllers.integration_health import (  # noqa: PLC0415
            IntegrationHealthController,
        )
        from synthorg.api.controllers.mcp_catalog import (  # noqa: PLC0415
            MCPCatalogController,
        )
        from synthorg.api.controllers.oauth import OAuthController  # noqa: PLC0415
        from synthorg.api.controllers.tunnel import (  # noqa: PLC0415
            TunnelController,
        )
        from synthorg.api.controllers.webhooks import (  # noqa: PLC0415
            WebhooksController,
        )

        controller_readiness: tuple[
            tuple[type[Controller], tuple[tuple[str, object], ...]], ...
        ] = (
            (
                ConnectionsController,
                (("connection_catalog", connection_catalog),),
            ),
            (
                IntegrationHealthController,
                (("connection_catalog", connection_catalog),),
            ),
            (
                OAuthController,
                (
                    ("connection_catalog", connection_catalog),
                    ("persistence", persistence),
                ),
            ),
            (
                WebhooksController,
                (
                    ("connection_catalog", connection_catalog),
                    ("message_bus", message_bus),
                ),
            ),
            (
                MCPCatalogController,
                (("mcp_catalog_service", mcp_catalog_service),),
            ),
            (
                TunnelController,
                (("tunnel_provider", tunnel_provider),),
            ),
        )
        ready: list[type[Controller]] = []
        for controller_cls, deps in controller_readiness:
            missing = [name for name, value in deps if value is None]
            if missing:
                logger.warning(
                    API_APP_STARTUP,
                    note="skipping integration controller (missing deps)",
                    controller=controller_cls.__name__,
                    missing=missing,
                )
                continue
            ready.append(controller_cls)
        integration_controllers = tuple(ready)

    # ── A2A gateway auto-wire ─────────────────────────────────────
    a2a_controllers: tuple[type[Controller], ...] = ()
    a2a_root_controllers: tuple[type[Controller], ...] = ()
    if effective_config.a2a.enabled:
        # Build every A2A artefact into local variables FIRST so an
        # exception anywhere in the construction chain leaves
        # ``app_state`` untouched. Only commit to ``app_state`` and
        # the controller tuples after every required object is
        # successfully constructed -- otherwise a half-wired surface
        # would survive a non-fatal failure (card builder set but
        # client absent, peer registry registered but gateway
        # controller missing, etc.).
        a2a_card_builder = None
        a2a_root_pending: tuple[type[Controller], ...] = ()
        a2a_pending: tuple[type[Controller], ...] = ()
        a2a_peer_registry = None
        a2a_client_obj = None
        try:
            from synthorg.a2a.agent_card import (  # noqa: PLC0415
                AgentCardBuilder,
            )
            from synthorg.a2a.models import A2AAuthSchemeInfo  # noqa: PLC0415
            from synthorg.a2a.well_known import (  # noqa: PLC0415
                WellKnownAgentCardController,
            )

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
            a2a_root_pending = (WellKnownAgentCardController,)

            # Outbound client + JSON-RPC gateway need the connection
            # catalog and integrations enabled.
            if effective_config.integrations.enabled and connection_catalog is not None:
                import httpx  # noqa: PLC0415

                from synthorg.a2a.client import A2AClient  # noqa: PLC0415
                from synthorg.a2a.gateway import (  # noqa: PLC0415
                    A2AGatewayController,
                )
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
                a2a_pending = (A2AGatewayController,)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_APP_STARTUP,
                note="A2A gateway auto-wire failed (non-fatal)",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            # Commit only on full success. Partial failures land in
            # the except branch above with all ``app_state`` slots
            # still empty.
            app_state.set_a2a_card_builder(a2a_card_builder)
            a2a_root_controllers = a2a_root_pending
            if a2a_peer_registry is not None and a2a_client_obj is not None:
                app_state.set_a2a_peer_registry(a2a_peer_registry)
                app_state.set_a2a_client(a2a_client_obj)
                a2a_controllers = a2a_pending
            logger.info(
                API_SERVICE_AUTO_WIRED,
                service="a2a_gateway",
            )

    # Default to a fresh ``ClientSimulationState()`` so the
    # always-registered ``ClientController`` can serve an empty
    # ``/clients`` list instead of 503ing on every dashboard poll.
    # Callers wanting the full intake / review pipeline pass a
    # configured state via the kwarg.
    if client_simulation_state is None:
        from synthorg.client.simulation_state import (  # noqa: PLC0415
            ClientSimulationState as _ClientSimulationState,
        )

        client_simulation_state = _ClientSimulationState()
    app_state.set_client_simulation_state(client_simulation_state)

    # Optional controllers gated on their primary collaborator service.
    # Routes for unconfigured subsystems are not registered at all so
    # the dashboard receives 404 (route does not exist) instead of the
    # 503 it used to get for every poll cycle.  /capabilities reports
    # which subsystems are wired so the dashboard can skip the polling
    # loops at the source. Fail loudly when a registered predicate name
    # is missing from AppState (typo / rename) -- silently disabling
    # routes via ``getattr(..., False)`` would turn a wiring bug into
    # an unnoticed 404 surface regression.
    _optional: list[type[Controller]] = []
    for controller_cls, predicate_attr in OPTIONAL_CONTROLLERS:
        if not hasattr(app_state, predicate_attr):
            msg = (
                f"Optional controller predicate {predicate_attr!r} is "
                f"missing on AppState (controller={controller_cls.__name__})."
            )
            logger.error(API_APP_STARTUP, note=msg, controller=controller_cls.__name__)
            raise RuntimeError(msg)
        if bool(getattr(app_state, predicate_attr)):
            _optional.append(controller_cls)
    optional_controllers: tuple[type[Controller], ...] = tuple(_optional)

    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=[
            *BASE_CONTROLLERS,
            *integration_controllers,
            *a2a_controllers,
            *optional_controllers,
            ws_handler,
        ],
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
        app_state.set_review_gate_service(review_gate_service)

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

    _worker_service_installed = False

    async def _install_worker_execution_service() -> None:
        # Installs the worker execution service behind the
        # provider-present switch. Appended first (runs immediately
        # after the core startup hooks that connect persistence and
        # wire SettingsService / ConfigResolver), and before any other
        # appended hook, so the once-only ``set_worker_execution_service``
        # cannot lose a race with the property's lazy lifecycle-only
        # default. With no provider this installs the empty-company
        # backstop; a provider added later swaps in the live service via
        # ``post_setup_reinit`` (no restart). The closure flag keeps the
        # one-shot ``set_`` idempotent across a lifespan re-entry
        # (shared-app test fixtures), mirroring ``_wire_chief_of_staff_chat``.
        nonlocal _worker_service_installed
        if _worker_service_installed:
            return
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            build_worker_execution_service,
        )

        # Pin the sandbox workspace onto the mounted data volume in an
        # env-driven deployment so agent file/sandbox tools persist with
        # the runtime data, not a process temp dir. Injected/dev apps
        # return None and keep the documented temp fallback.
        env_workspace_root = resolve_agent_workspace_root_env()
        if env_workspace_root is not None:
            app_state.set_agent_workspace_root(env_workspace_root)

        try:
            service = await build_worker_execution_service(
                app_state,
                workspace_root=app_state.agent_workspace_root,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                API_APP_STARTUP,
                service="worker_execution_service",
                note="failed to build the worker execution service at boot",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        app_state.set_worker_execution_service(service)
        _worker_service_installed = True

    startup = [*startup, _install_worker_execution_service]

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
    app_state.set_telemetry_collector(telemetry_collector)
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
        app_state.set_report_service(report_service)

    async def _wire_chief_of_staff_chat() -> None:
        # Wired only when the meta config opts in via
        # ``chief_of_staff.chat_enabled`` AND a provider is registered.
        # When unwired, ``POST /meta/chat`` surfaces 503 rather than the
        # silent placeholder it returned previously.
        # Idempotent: a re-entry of lifespan startup against the same
        # ``AppState`` (e.g. ASGI restart in tests) would otherwise make
        # the one-shot ``set_chief_of_staff_chat`` raise.
        if app_state.has_chief_of_staff_chat:
            return
        if provider_registry is None:
            return
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )

        meta_self_improvement = await load_self_improvement_config(
            app_state.settings_service if app_state.has_settings_service else None,
        )
        chat_backend = build_chief_of_staff_chat(
            meta_self_improvement.chief_of_staff,
            provider_registry=provider_registry,
            cost_tracker=cost_tracker,
        )
        if chat_backend is not None:
            app_state.set_chief_of_staff_chat(chat_backend)

    startup = [*startup, _wire_chief_of_staff_chat]

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
        # Each security key resolves independently so a validation
        # failure on an unrelated ``api.*`` field (e.g. a bad
        # ``request_max_body_size_bytes``) does not silently suppress
        # CSP-origin or error-docs overrides. The shared
        # ``ApiBridgeConfig`` validator still runs per key by
        # constructing a one-field model -- defaults satisfy the
        # remaining fields without re-resolving them.
        # Failure branches must actively re-write the module global to
        # ``ApiBridgeConfig()`` defaults, not just log "fallback":
        # a previous app instance (or earlier test on the same worker)
        # may have already mutated the global, in which case skipping
        # the write would silently keep a stale override instead of
        # the documented default.
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            ApiBridgeConfig,
        )

        defaults = ApiBridgeConfig()

        if not app_state.has_config_resolver:
            set_docs_csp_origins(defaults.csp_docs_external_origins)
            set_error_docs_base_url(defaults.error_docs_base_url)
            logger.warning(
                API_BRIDGE_CONFIG_RESOLVE_FAILED,
                bridge="api",
                reason="config_resolver_unavailable",
                fallback="module_defaults",
            )
            return
        resolver = app_state.config_resolver

        try:
            origins_raw = await resolver.get_json("api", "csp_docs_external_origins")
            # Pass the raw JSON shape directly so ApiBridgeConfig sees
            # the unmodified payload. ``tuple(...)`` would coerce a
            # mapping to its keys (and other non-iterable shapes to
            # TypeError), masking the real validation failure. Pydantic
            # returns a ``tuple[str, ...]`` after its own validation
            # runs, so ``set_docs_csp_origins`` still receives the
            # correct shape.
            csp_bridge = ApiBridgeConfig(csp_docs_external_origins=origins_raw)
            set_docs_csp_origins(csp_bridge.csp_docs_external_origins)
        except (
            SettingNotFoundError,
            SettingsEncryptionError,
            ValueError,
            ValidationError,
        ) as exc:
            set_docs_csp_origins(defaults.csp_docs_external_origins)
            logger.warning(
                API_BRIDGE_CONFIG_RESOLVE_FAILED,
                bridge="api",
                key="csp_docs_external_origins",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback="module_default",
            )

        try:
            url_raw = await resolver.get_str("api", "error_docs_base_url")
            error_bridge = ApiBridgeConfig(error_docs_base_url=url_raw)
            set_error_docs_base_url(error_bridge.error_docs_base_url)
            logger.info(
                SETTINGS_VALUE_RESOLVED,
                namespace="api",
                key="error_docs_base_url",
                value=error_bridge.error_docs_base_url,
            )
        except (
            SettingNotFoundError,
            SettingsEncryptionError,
            ValueError,
            ValidationError,
        ) as exc:
            set_error_docs_base_url(defaults.error_docs_base_url)
            logger.warning(
                API_BRIDGE_CONFIG_RESOLVE_FAILED,
                bridge="api",
                key="error_docs_base_url",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback="module_default",
            )

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
        route_handlers=[api_router, *a2a_root_controllers],
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
