"""Uvicorn server runner.

Provides a convenience function to start the API server
with settings from ``RootConfig``.
"""

from typing import TYPE_CHECKING

import uvicorn

from synthorg.api.app import create_app
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)


def run_server(config: RootConfig) -> None:
    """Create and run the Litestar app via uvicorn.

    Backend services (persistence, message bus, cost tracker) are
    not passed — intended for development/demo use.  For production,
    call ``create_app()`` directly with all services.

    Args:
        config: Root company configuration containing server
            settings.
    """
    api_config = config.api
    server = api_config.server

    logger.info(
        API_APP_STARTUP,
        host=server.host,
        port=server.port,
        workers=server.workers,
    )

    ws_ping: float | None = (
        server.ws_ping_interval if server.ws_ping_interval > 0 else None
    )
    ws_timeout: float | None = (
        server.ws_ping_timeout if server.ws_ping_timeout > 0 else None
    )

    app = create_app(config=config)
    uvicorn.run(
        app,
        host=server.host,
        port=server.port,
        workers=server.workers,
        reload=server.reload,
        ws_ping_interval=ws_ping,
        ws_ping_timeout=ws_timeout,
    )
