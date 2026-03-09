"""Uvicorn server runner.

Provides a convenience function to start the API server
with settings from ``RootConfig``.
"""

from typing import TYPE_CHECKING

import uvicorn

from ai_company.api.config import ApiConfig

if TYPE_CHECKING:
    from ai_company.config.schema import RootConfig


def run_server(config: RootConfig) -> None:
    """Start the API server via uvicorn.

    Args:
        config: Root company configuration containing server
            settings.
    """
    api_config: ApiConfig = getattr(config, "api", None) or ApiConfig()
    server = api_config.server

    uvicorn.run(
        "ai_company.api.app:create_app",
        host=server.host,
        port=server.port,
        reload=server.reload,
        workers=server.workers,
        factory=True,
    )
