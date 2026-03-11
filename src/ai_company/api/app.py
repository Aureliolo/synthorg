"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from litestar import Litestar, Router
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import ResponseHeader, State
from litestar.middleware.rate_limit import RateLimitConfig as LitestarRateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin

from ai_company import __version__
from ai_company.api.approval_store import ApprovalStore
from ai_company.api.auth.controller import require_password_changed
from ai_company.api.auth.middleware import create_auth_middleware_class
from ai_company.api.auth.secret import resolve_jwt_secret
from ai_company.api.auth.service import AuthService
from ai_company.api.bus_bridge import MessageBusBridge
from ai_company.api.channels import CHANNEL_APPROVALS, create_channels_plugin
from ai_company.api.controllers import ALL_CONTROLLERS
from ai_company.api.controllers.ws import ws_handler
from ai_company.api.exception_handlers import EXCEPTION_HANDLERS
from ai_company.api.middleware import CSPMiddleware, RequestLoggingMiddleware
from ai_company.api.state import AppState
from ai_company.api.ws_models import WsEvent, WsEventType
from ai_company.budget.tracker import CostTracker  # noqa: TC001
from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.config.schema import RootConfig
from ai_company.core.approval import ApprovalItem  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_STARTUP,
    API_APPROVAL_PUBLISH_FAILED,
)
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from litestar.channels import ChannelsPlugin
    from litestar.types import Middleware

    from ai_company.api.config import ApiConfig

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
        except RuntimeError, OSError:
            logger.warning(
                API_APPROVAL_PUBLISH_FAILED,
                approval_id=item.id,
                event_type=WsEventType.APPROVAL_EXPIRED.value,
                exc_info=True,
            )

    return _on_expire


def _build_lifecycle(
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
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
        await _safe_startup(persistence, message_bus, bridge, app_state)

    async def on_shutdown() -> None:
        logger.info(API_APP_SHUTDOWN, version=__version__)
        await _safe_shutdown(bridge, message_bus, persistence)

    return [on_startup], [on_shutdown]


async def _cleanup_on_failure(
    *,
    persistence: PersistenceBackend | None,
    started_persistence: bool,
    message_bus: MessageBus | None,
    started_bus: bool,
) -> None:
    """Reverse cleanup of persistence and message bus on startup failure."""
    if started_bus and message_bus is not None:
        try:
            await message_bus.stop()
        except Exception:
            logger.exception(
                API_APP_STARTUP,
                error="Cleanup: failed to stop message bus",
            )
    if started_persistence and persistence is not None:
        try:
            await persistence.disconnect()
        except Exception:
            logger.exception(
                API_APP_STARTUP,
                error="Cleanup: failed to disconnect persistence",
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


async def _safe_startup(
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    app_state: AppState,
) -> None:
    """Connect persistence, resolve JWT secret, start message bus and bridge.

    Executes in order; on failure, cleans up already-started
    components in reverse order before re-raising.
    """
    started_bus = False
    started_persistence = False
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
    except Exception:
        await _cleanup_on_failure(
            persistence=persistence,
            started_persistence=started_persistence,
            message_bus=message_bus,
            started_bus=started_bus,
        )
        raise


async def _safe_shutdown(
    bridge: MessageBusBridge | None,
    message_bus: MessageBus | None,
    persistence: PersistenceBackend | None,
) -> None:
    """Stop bridge, message bus and disconnect persistence."""
    if bridge is not None:
        try:
            await bridge.stop()
        except Exception:
            logger.exception(
                API_APP_SHUTDOWN,
                error="Failed to stop message bus bridge",
            )
    if message_bus is not None:
        try:
            await message_bus.stop()
        except Exception:
            logger.exception(
                API_APP_SHUTDOWN,
                error="Failed to stop message bus",
            )
    if persistence is not None:
        try:
            await persistence.disconnect()
        except Exception:
            logger.exception(
                API_APP_SHUTDOWN,
                error="Failed to disconnect persistence",
            )


def create_app(  # noqa: PLR0913
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
    approval_store: ApprovalStore | None = None,
    auth_service: AuthService | None = None,
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

    Returns:
        Configured Litestar application.
    """
    effective_config = config or RootConfig(company_name="default")
    api_config = effective_config.api

    if persistence is None or message_bus is None or cost_tracker is None:
        msg = (
            "create_app called without persistence, message_bus, "
            "and/or cost_tracker — controllers accessing missing "
            "services will return 503.  Use test fakes for testing."
        )
        logger.warning(API_APP_STARTUP, note=msg)

    channels_plugin = create_channels_plugin()
    expire_callback = _make_expire_callback(channels_plugin)
    effective_approval_store = approval_store or ApprovalStore(
        on_expire=expire_callback,
    )

    app_state = AppState(
        config=effective_config,
        persistence=persistence,
        message_bus=message_bus,
        cost_tracker=cost_tracker,
        approval_store=effective_approval_store,
        auth_service=auth_service,
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
        ],
        middleware=middleware,
        plugins=plugins,
        exception_handlers=EXCEPTION_HANDLERS,  # type: ignore[arg-type]
        openapi_config=OpenAPIConfig(
            title="AI Company API",
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
