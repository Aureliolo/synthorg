"""Uvicorn server runner.

Provides a convenience function to start the API server
with settings from ``RootConfig``.
"""

from typing import TYPE_CHECKING, Any

import uvicorn

from synthorg.api.app import create_app
from synthorg.api.drain import RequestDrainMiddleware
from synthorg.api.lifecycle import _DRAIN_TIMEOUT_SECONDS
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_TLS_CONFIGURED,
)

if TYPE_CHECKING:
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)


def run_server(config: RootConfig) -> None:
    """Create and run the Litestar app via uvicorn.

    Backend services are auto-wired by ``create_app()`` from
    configuration and environment variables (e.g. ``SYNTHORG_DB_PATH``).
    Explicit service injection is only needed for testing.

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

    ssl_kwargs: dict[str, Any] = {}
    if server.ssl_certfile:
        ssl_kwargs["ssl_certfile"] = server.ssl_certfile
        ssl_kwargs["ssl_keyfile"] = server.ssl_keyfile
        if server.ssl_ca_certs:
            ssl_kwargs["ssl_ca_certs"] = server.ssl_ca_certs
        logger.info(
            API_TLS_CONFIGURED,
            certfile=server.ssl_certfile,
        )

    proxy_kwargs: dict[str, Any] = {}
    if server.trusted_proxies:
        proxy_kwargs["forwarded_allow_ips"] = ",".join(
            server.trusted_proxies,
        )
        proxy_kwargs["proxy_headers"] = True

    app = create_app(config=config)
    # Wrap the Litestar app in the request-drain middleware as the
    # outermost ASGI layer.  The wrap happens here rather than in
    # ``create_app`` so unit tests retrieve a raw ``Litestar`` for
    # ``TestClient``; production uvicorn always gets the drain
    # wrapper.  The drain middleware itself intercepts
    # ``lifespan.shutdown`` and runs ``begin_drain`` before
    # forwarding the message to Litestar, so the per-service
    # ``on_shutdown`` hooks only start once in-flight HTTP traffic
    # has drained.
    drain_app = RequestDrainMiddleware(
        app,
        drain_timeout_seconds=_DRAIN_TIMEOUT_SECONDS,
    )
    uvicorn.run(
        drain_app,
        host=server.host,
        port=server.port,
        workers=server.workers,
        reload=server.reload,
        ws_ping_interval=ws_ping,
        ws_ping_timeout=ws_timeout,
        access_log=False,
        log_config=None,
        # Internal constant by design.  api/lifecycle.py per-service
        # budgets sum to ~67 s worst case (25 s in-process drain +
        # 42 s services, where the 25 s is already counted inside
        # the 67 s total).  This 75 s uvicorn timeout matches the
        # orchestrator's ``terminationGracePeriodSeconds: 75`` and
        # leaves ~8 s of headroom (75 - 67) before SIGKILL fires
        # mid-teardown.  Raising this without also raising the
        # per-service budgets does not buy more time -- the
        # orchestrator kills the process at the same instant.
        # Lowering it surrenders the 8 s headroom back to SIGKILL.
        # Not exposed to the settings registry; see
        # ``docs/design/deployment.md`` for the full math.
        timeout_graceful_shutdown=75,
        **ssl_kwargs,
        **proxy_kwargs,
    )
