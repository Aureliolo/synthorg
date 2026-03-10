"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import time
from collections.abc import Callable
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
        await _safe_startup(persistence, message_bus, bridge)

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
            logger.error(
                API_APP_STARTUP,
                error="Cleanup: failed to stop message bus",
                exc_info=True,
            )
    if started_persistence and persistence is not None:
        try:
            await persistence.disconnect()
        except Exception:
            logger.error(
                API_APP_STARTUP,
                error="Cleanup: failed to disconnect persistence",
                exc_info=True,
            )


async def _safe_startup(
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
) -> None:
    """Connect persistence, start message bus and bridge.

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
                logger.error(
                    API_APP_STARTUP,
                    error="Failed to connect persistence",
                    exc_info=True,
                )
                raise
            started_persistence = True
        if message_bus is not None:
            try:
                await message_bus.start()
            except Exception:
                logger.error(
                    API_APP_STARTUP,
                    error="Failed to start message bus",
                    exc_info=True,
                )
                raise
            started_bus = True
        if bridge is not None:
            try:
                await bridge.start()
            except Exception:
                logger.error(
                    API_APP_STARTUP,
                    error="Failed to start message bus bridge",
                    exc_info=True,
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
            logger.error(
                API_APP_SHUTDOWN,
                error="Failed to stop message bus bridge",
                exc_info=True,
            )
    if message_bus is not None:
        try:
            await message_bus.stop()
        except Exception:
            logger.error(
                API_APP_SHUTDOWN,
                error="Failed to stop message bus",
                exc_info=True,
            )
    if persistence is not None:
        try:
            await persistence.disconnect()
        except Exception:
            logger.error(
                API_APP_SHUTDOWN,
                error="Failed to disconnect persistence",
                exc_info=True,
            )


def create_app(
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
    approval_store: ApprovalStore | None = None,
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
        startup_time=time.monotonic(),
    )

    bridge = _build_bridge(message_bus, channels_plugin)
    plugins: list[ChannelsPlugin] = [channels_plugin]
    middleware = _build_middleware(api_config)

    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=[*ALL_CONTROLLERS, ws_handler],
    )

    startup, shutdown = _build_lifecycle(
        persistence,
        message_bus,
        bridge,
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
        middleware=middleware,  # type: ignore[arg-type]
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


def _build_middleware(api_config: ApiConfig) -> list[object]:
    """Build the middleware stack from configuration."""
    rl = api_config.rate_limit
    rate_limit = LitestarRateLimitConfig(
        rate_limit=(rl.time_unit, rl.max_requests),  # type: ignore[arg-type]
        exclude=list(rl.exclude_paths),
    )
    return [CSPMiddleware, RequestLoggingMiddleware, rate_limit.middleware]
