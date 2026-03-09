"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, and plugins.
"""

from litestar import Litestar, Router
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import (
    RedocRenderPlugin,
    ScalarRenderPlugin,
)

from ai_company import __version__
from ai_company.api.config import ApiConfig
from ai_company.api.controllers import ALL_CONTROLLERS
from ai_company.api.exception_handlers import EXCEPTION_HANDLERS
from ai_company.api.middleware import RequestLoggingMiddleware
from ai_company.api.state import AppState
from ai_company.budget.tracker import CostTracker  # noqa: TC001
from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.config.schema import RootConfig
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_STARTUP,
)
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001

logger = get_logger(__name__)


def create_app(
    *,
    config: RootConfig | None = None,
    persistence: PersistenceBackend | None = None,
    message_bus: MessageBus | None = None,
    cost_tracker: CostTracker | None = None,
) -> Litestar:
    """Create and configure the Litestar application.

    All parameters are optional for testing — provide fakes via
    keyword arguments.

    Args:
        config: Root company configuration.
        persistence: Persistence backend.
        message_bus: Internal message bus.
        cost_tracker: Cost tracking service.

    Returns:
        Configured Litestar application.
    """
    effective_config = config or RootConfig(company_name="default")
    api_config = getattr(effective_config, "api", None) or ApiConfig()

    if persistence is None or message_bus is None or cost_tracker is None:
        msg = (
            "create_app requires persistence, message_bus, and "
            "cost_tracker in production; use test fakes for "
            "testing"
        )
        logger.warning(API_APP_STARTUP, note=msg)

    app_state = AppState(
        config=effective_config,
        persistence=persistence,  # type: ignore[arg-type]
        message_bus=message_bus,  # type: ignore[arg-type]
        cost_tracker=cost_tracker,  # type: ignore[arg-type]
    )

    cors_config = CORSConfig(
        allow_origins=list(api_config.cors.allowed_origins),
        allow_methods=list(api_config.cors.allow_methods),  # type: ignore[arg-type]
        allow_headers=list(api_config.cors.allow_headers),
        allow_credentials=api_config.cors.allow_credentials,
    )

    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=list(ALL_CONTROLLERS),
    )

    async def on_startup() -> None:
        logger.info(API_APP_STARTUP, version=__version__)
        if persistence is not None:
            await persistence.connect()
        if message_bus is not None:
            await message_bus.start()

    async def on_shutdown() -> None:
        logger.info(API_APP_SHUTDOWN, version=__version__)
        if message_bus is not None:
            await message_bus.stop()
        if persistence is not None:
            await persistence.disconnect()

    return Litestar(
        route_handlers=[api_router],
        state=State({"app_state": app_state}),
        cors_config=cors_config,
        compression_config=CompressionConfig(
            backend="gzip",
            minimum_size=1000,
        ),
        middleware=[RequestLoggingMiddleware],
        exception_handlers=EXCEPTION_HANDLERS,  # type: ignore[arg-type]
        openapi_config=OpenAPIConfig(
            title="AI Company API",
            version=__version__,
            render_plugins=[
                ScalarRenderPlugin(path="/docs"),
                RedocRenderPlugin(path="/redoc"),
            ],
        ),
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )
