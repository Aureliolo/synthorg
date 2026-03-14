"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from litestar import Litestar, Router
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import ResponseHeader, State
from litestar.middleware.rate_limit import RateLimitConfig as LitestarRateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin

from synthorg import __version__
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.controller import require_password_changed
from synthorg.api.auth.middleware import create_auth_middleware_class
from synthorg.api.auth.secret import resolve_jwt_secret
from synthorg.api.auth.service import AuthService
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import (
    CHANNEL_APPROVALS,
    CHANNEL_MEETINGS,
    create_channels_plugin,
)
from synthorg.api.controllers import ALL_CONTROLLERS
from synthorg.api.controllers.ws import ws_handler
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.middleware import CSPMiddleware, RequestLoggingMiddleware
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,  # noqa: TC001
)
from synthorg.communication.meeting.scheduler import MeetingScheduler  # noqa: TC001
from synthorg.config.schema import RootConfig
from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.engine.coordination.service import MultiAgentCoordinator  # noqa: TC001
from synthorg.engine.task_engine import TaskEngine  # noqa: TC001
from synthorg.hr.registry import AgentRegistryService  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_STARTUP,
    API_APPROVAL_PUBLISH_FAILED,
)
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from litestar.channels import ChannelsPlugin
    from litestar.types import Middleware

    from synthorg.api.config import ApiConfig

logger = get_logger(__name__)


def _make_expire_callback(
    channels_plugin: ChannelsPlugin,
) -> Callable[[ApprovalItem], None]:
    """Create a sync callback that publishes APPROVAL_EXPIRED events.

    The callback is invoked by ``ApprovalStore._check_expiration``
    when lazy expiry transitions an item to EXPIRED.  Best-effort:
    publish errors are logged and swallowed.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.

    Returns:
        Sync callback accepting an expired ``ApprovalItem``.
    """

    def _on_expire(item: ApprovalItem) -> None:
        event = WsEvent(
            event_type=WsEventType.APPROVAL_EXPIRED,
            channel=CHANNEL_APPROVALS,
            timestamp=datetime.now(UTC),
            payload={
                "approval_id": item.id,
                "status": item.status.value,
                "action_type": item.action_type,
                "risk_level": item.risk_level.value,
            },
        )
        try:
            channels_plugin.publish(
                event.model_dump_json(),
                channels=[CHANNEL_APPROVALS],
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                API_APPROVAL_PUBLISH_FAILED,
                approval_id=item.id,
                event_type=WsEventType.APPROVAL_EXPIRED.value,
                exc_info=True,
            )

    return _on_expire


def _make_meeting_publisher(
    channels_plugin: ChannelsPlugin,
) -> Callable[[str, dict[str, Any]], None]:
    """Create a sync callback that publishes meeting events to WS.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.

    Returns:
        Sync callback ``(event_name, payload) -> None``.
    """

    def _on_meeting_event(
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        event = WsEvent(
            event_type=WsEventType(event_name),
            channel=CHANNEL_MEETINGS,
            timestamp=datetime.now(UTC),
            payload=payload,
        )
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[CHANNEL_MEETINGS],
        )

    return _on_meeting_event


def _build_lifecycle(  # noqa: PLR0913
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    app_state: AppState,
) -> tuple[
    Sequence[Callable[[], Awaitable[None]]],
    Sequence[Callable[[], Awaitable[None]]],
]:
    """Build startup and shutdown hooks.

    Returns:
        A tuple of (on_startup, on_shutdown) callback lists.
    """

    async def on_startup() -> None:
        logger.info(API_APP_STARTUP, version=__version__)
        await _safe_startup(
            persistence,
            message_bus,
            bridge,
            task_engine,
            meeting_scheduler,
            app_state,
        )

    async def on_shutdown() -> None:
        logger.info(API_APP_SHUTDOWN, version=__version__)
        await _safe_shutdown(
            task_engine,
            meeting_scheduler,
            bridge,
            message_bus,
            persistence,
        )

    return [on_startup], [on_shutdown]


async def _try_stop(
    coro: Awaitable[None],
    event: str,
    error_msg: str,
) -> None:
    """Await *coro* inside a safe try/except, logging failures.

    ``MemoryError`` and ``RecursionError`` are re-raised immediately;
    all other exceptions are logged and swallowed so that sibling
    shutdown steps can still run.
    """
    try:
        await coro
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.exception(event, error=error_msg)


async def _cleanup_on_failure(  # noqa: PLR0913
    *,
    persistence: PersistenceBackend | None,
    started_persistence: bool,
    message_bus: MessageBus | None,
    started_bus: bool,
    bridge: MessageBusBridge | None = None,
    started_bridge: bool = False,
    task_engine: TaskEngine | None = None,
    started_task_engine: bool = False,
    meeting_scheduler: MeetingScheduler | None = None,
    started_meeting_scheduler: bool = False,
) -> None:
    """Reverse cleanup on startup failure."""
    if started_meeting_scheduler and meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop meeting scheduler",
        )
    if started_task_engine and task_engine is not None:
        await _try_stop(
            task_engine.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop task engine",
        )
    if started_bridge and bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus bridge",
        )
    if started_bus and message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus",
        )
    if started_persistence and persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_STARTUP,
            "Cleanup: failed to disconnect persistence",
        )


async def _init_persistence(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Run migrations and resolve JWT secret on an already-connected backend.

    Must only be called after ``persistence.connect()`` has succeeded.

    Args:
        persistence: Connected persistence backend.
        app_state: Application state for auth service injection.
    """
    try:
        await persistence.migrate()
    except Exception:
        logger.exception(
            API_APP_STARTUP,
            error="Failed to run persistence migrations",
        )
        raise

    # Resolve JWT secret after persistence is up
    if app_state.has_auth_service:
        logger.info(
            API_APP_STARTUP,
            note="Auth service already configured, skipping JWT secret resolution",
        )
    else:
        try:
            secret = await resolve_jwt_secret(persistence)
            auth_config = app_state.config.api.auth.with_secret(
                secret,
            )
            app_state.set_auth_service(AuthService(auth_config))
        except Exception:
            logger.exception(
                API_APP_STARTUP,
                error="Failed to resolve JWT secret",
            )
            raise


async def _safe_startup(  # noqa: PLR0913, C901
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    app_state: AppState,
) -> None:
    """Start all services: persistence, bus, bridge, task engine, scheduler.

    Executes in order; on failure, cleans up already-started
    components in reverse order before re-raising.
    """
    started_bus = False
    started_bridge = False
    started_persistence = False
    started_task_engine = False
    started_meeting_scheduler = False
    try:
        if persistence is not None:
            try:
                await persistence.connect()
            except Exception:
                logger.exception(
                    API_APP_STARTUP,
                    error="Failed to connect persistence",
                )
                raise
            # Mark connected immediately so cleanup can disconnect
            # if migrate() or JWT resolution fails below.
            started_persistence = True
            await _init_persistence(persistence, app_state)

        if message_bus is not None:
            try:
                await message_bus.start()
            except Exception:
                logger.exception(
                    API_APP_STARTUP,
                    error="Failed to start message bus",
                )
                raise
            started_bus = True
        if bridge is not None:
            try:
                await bridge.start()
            except Exception:
                logger.exception(
                    API_APP_STARTUP,
                    error="Failed to start message bus bridge",
                )
                raise
            started_bridge = True
        if task_engine is not None:
            try:
                task_engine.start()
            except Exception:
                logger.exception(
                    API_APP_STARTUP,
                    error="Failed to start task engine",
                )
                raise
            started_task_engine = True
        if meeting_scheduler is not None:
            try:
                await meeting_scheduler.start()
            except Exception:
                logger.exception(
                    API_APP_STARTUP,
                    error="Failed to start meeting scheduler",
                )
                raise
            started_meeting_scheduler = True
    except Exception:
        await _cleanup_on_failure(
            persistence=persistence,
            started_persistence=started_persistence,
            message_bus=message_bus,
            started_bus=started_bus,
            bridge=bridge,
            started_bridge=started_bridge,
            task_engine=task_engine,
            started_task_engine=started_task_engine,
            meeting_scheduler=meeting_scheduler,
            started_meeting_scheduler=started_meeting_scheduler,
        )
        raise


async def _safe_shutdown(
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    bridge: MessageBusBridge | None,
    message_bus: MessageBus | None,
    persistence: PersistenceBackend | None,
) -> None:
    """Stop scheduler, task engine, bridge, message bus and disconnect persistence.

    Mirrors ``_cleanup_on_failure`` reverse order: scheduler first (depends on
    orchestrator), then task engine so it can drain queued mutations and
    publish final snapshots through the still-running bridge.
    """
    if meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop meeting scheduler",
        )
    if task_engine is not None:
        await _try_stop(
            task_engine.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop task engine",
        )
    if bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus bridge",
        )
    if message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus",
        )
    if persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_SHUTDOWN,
            "Failed to disconnect persistence",
        )


def create_app(  # noqa: PLR0913
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
    approval_store: ApprovalStore | None = None,
    auth_service: AuthService | None = None,
    task_engine: TaskEngine | None = None,
    coordinator: MultiAgentCoordinator | None = None,
    agent_registry: AgentRegistryService | None = None,
    meeting_orchestrator: MeetingOrchestrator | None = None,
    meeting_scheduler: MeetingScheduler | None = None,
) -> Litestar:
    """Create and configure the Litestar application.

    All parameters are optional for testing — provide fakes via
    keyword arguments.

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

    Returns:
        Configured Litestar application.
    """
    effective_config = config or RootConfig(company_name="default")
    api_config = effective_config.api

    if (
        persistence is None
        or message_bus is None
        or cost_tracker is None
        or task_engine is None
    ):
        msg = (
            "create_app called without persistence, message_bus, "
            "cost_tracker, and/or task_engine — controllers accessing "
            "missing services will return 503.  Use test fakes for testing."
        )
        logger.warning(API_APP_STARTUP, note=msg)

    channels_plugin = create_channels_plugin()
    expire_callback = _make_expire_callback(channels_plugin)
    effective_approval_store = approval_store or ApprovalStore(
        on_expire=expire_callback,
    )

    # Wire meeting event publisher to the meetings WS channel.
    if meeting_scheduler is not None and meeting_scheduler._event_publisher is None:  # noqa: SLF001
        meeting_scheduler._event_publisher = _make_meeting_publisher(  # noqa: SLF001
            channels_plugin,
        )

    app_state = AppState(
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
        startup_time=time.monotonic(),
    )

    bridge = _build_bridge(message_bus, channels_plugin)
    plugins: list[ChannelsPlugin] = [channels_plugin]
    middleware = _build_middleware(api_config)

    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=[*ALL_CONTROLLERS, ws_handler],
        guards=[require_password_changed],
    )

    startup, shutdown = _build_lifecycle(
        persistence,
        message_bus,
        bridge,
        task_engine,
        meeting_scheduler,
        app_state,
    )

    return Litestar(
        route_handlers=[api_router],
        state=State({"app_state": app_state}),
        cors_config=CORSConfig(
            allow_origins=list(api_config.cors.allowed_origins),
            allow_methods=list(api_config.cors.allow_methods),  # type: ignore[arg-type]
            allow_headers=list(api_config.cors.allow_headers),
            allow_credentials=api_config.cors.allow_credentials,
        ),
        compression_config=CompressionConfig(
            backend="brotli",
            minimum_size=1000,
        ),
        response_headers=[
            ResponseHeader(
                name="X-Content-Type-Options",
                value="nosniff",
            ),
            ResponseHeader(
                name="X-Frame-Options",
                value="DENY",
            ),
            ResponseHeader(
                name="Referrer-Policy",
                value="strict-origin-when-cross-origin",
            ),
            ResponseHeader(
                name="Strict-Transport-Security",
                value="max-age=63072000; includeSubDomains",
            ),
            ResponseHeader(
                name="Permissions-Policy",
                value="geolocation=(), camera=(), microphone=()",
            ),
            ResponseHeader(
                name="Cross-Origin-Resource-Policy",
                value="same-origin",
            ),
            ResponseHeader(
                name="Cache-Control",
                value="no-store",
            ),
        ],
        middleware=middleware,
        plugins=plugins,
        exception_handlers=EXCEPTION_HANDLERS,  # type: ignore[arg-type]
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


def _build_bridge(
    message_bus: MessageBus | None,
    channels_plugin: ChannelsPlugin,
) -> MessageBusBridge | None:
    """Create message bus bridge if bus is available."""
    if message_bus is None:
        return None
    return MessageBusBridge(message_bus, channels_plugin)


def _build_middleware(api_config: ApiConfig) -> list[Middleware]:
    """Build the middleware stack from configuration."""
    rl = api_config.rate_limit
    rate_limit = LitestarRateLimitConfig(
        rate_limit=(rl.time_unit, rl.max_requests),  # type: ignore[arg-type]
        exclude=list(rl.exclude_paths),
    )
    auth = api_config.auth
    if auth.exclude_paths is None:
        prefix = api_config.api_prefix
        auth = auth.model_copy(
            update={
                "exclude_paths": (
                    f"^{prefix}/health$",
                    "^/docs",
                    "^/api$",
                    f"^{prefix}/auth/setup$",
                    f"^{prefix}/auth/login$",
                ),
            },
        )
    auth_middleware = create_auth_middleware_class(auth)
    return [
        auth_middleware,
        CSPMiddleware,
        RequestLoggingMiddleware,
        rate_limit.middleware,
    ]
