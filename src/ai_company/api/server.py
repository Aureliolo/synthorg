"""Uvicorn server runner.

Provides a convenience function to start the API server
with settings from ``RootConfig``.
"""

from typing import TYPE_CHECKING

import uvicorn

from ai_company.api.config import ApiConfig
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from ai_company.config.schema import RootConfig

logger = get_logger(__name__)


def run_server(config: RootConfig) -> None:
    """Start the API server via uvicorn.

    Args:
        config: Root company configuration containing server
            settings.
    """
    api_config: ApiConfig = getattr(config, "api", None) or ApiConfig()
    server = api_config.server

    logger.info(
        API_APP_STARTUP,
        host=server.host,
        port=server.port,
        workers=server.workers,
        reload=server.reload,
    )

    uvicorn.run(
        "ai_company.api.app:create_app",
        host=server.host,
        port=server.port,
        reload=server.reload,
        workers=server.workers,
        factory=True,
    )
