"""Uvicorn server runner.

Provides a convenience function to start the API server
with settings from ``RootConfig``.
"""

from typing import TypedDict

import uvicorn

from synthorg.api.app import create_app
from synthorg.api.drain import RequestDrainMiddleware
from synthorg.api.lifecycle import _DRAIN_TIMEOUT_SECONDS
from synthorg.config.schema import RootConfig
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_TLS_CONFIGURED,
)
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_int, parse_str_tuple_json

logger = get_logger(__name__)

# Import string for the drain-wrapped ASGI factory. uvicorn needs an
# import string (not a pre-built object) to spawn worker subprocesses or
# the reloader; each child imports and calls this factory afresh.
_DRAIN_APP_FACTORY_PATH: str = "synthorg.api.server:create_drain_app"


def create_drain_app() -> RequestDrainMiddleware:
    """Build the drain-wrapped ASGI app from the environment-derived config.

    This is the uvicorn ``--factory`` entry used when ``workers > 1`` or
    ``reload`` is set: worker subprocesses cannot receive the pre-built
    object passed to a single-process run, so they rebuild the app from
    configuration the same way the production container's
    ``create_app --factory`` launch does. The drain middleware is applied
    here so multi-worker deployments retain in-flight request draining on
    shutdown.

    Returns:
        The Litestar app wrapped in :class:`RequestDrainMiddleware`.
    """
    return RequestDrainMiddleware(
        create_app(),
        drain_timeout_seconds=_DRAIN_TIMEOUT_SECONDS,
    )


class _OptionalUvicornKwargs(TypedDict, total=False):
    """Optional ``uvicorn.run`` kwargs set only when configured.

    Splatting a ``TypedDict`` lets mypy validate each key against the
    ``uvicorn.run`` signature, unlike a ``dict[str, Any]`` splat.
    """

    ssl_certfile: str
    ssl_keyfile: str | None
    ssl_ca_certs: str
    forwarded_allow_ips: str
    proxy_headers: bool


def run_server(config: RootConfig) -> None:
    """Create and run the Litestar app via uvicorn.

    Backend services are auto-wired by ``create_app()`` from
    configuration and environment variables (e.g. ``SYNTHORG_DB_PATH``).
    Explicit service injection is only needed for testing.

    Args:
        config: Root company configuration containing server
            settings.

    Raises:
        ValueError: If ``reload`` is enabled with more than one worker
            (the uvicorn reloader is single-process only).
    """
    api_config = config.api
    server = api_config.server

    # uvicorn runs the reloader in a single supervised process; pairing
    # it with multiple workers is unsupported and silently drops one or
    # the other. Reject the combination loudly instead of booting a
    # surprising topology.
    if server.reload and server.workers > 1:
        msg = (
            "api.server.reload requires workers == 1 "
            f"(got workers={server.workers}); reload is single-process only"
        )
        raise ValueError(msg)

    def _str_or_none(key: str) -> str | None:
        resolved = resolve_init_value(SettingNamespace.API, key)
        raw = str(resolved.value).strip()
        return raw or None

    def _str_tuple(key: str) -> tuple[str, ...]:
        resolved = resolve_init_value(
            SettingNamespace.API,
            key,
            parse=parse_str_tuple_json,
        )
        if isinstance(resolved.value, tuple):
            return resolved.value
        return ()

    host = str(resolve_init_value(SettingNamespace.API, "server_host").value)
    port = int(
        resolve_init_value(
            SettingNamespace.API,
            "server_port",
            parse=parse_int,
        ).value
    )
    ssl_certfile = _str_or_none("ssl_certfile")
    ssl_keyfile = _str_or_none("ssl_keyfile")
    ssl_ca_certs = _str_or_none("ssl_ca_certs")
    trusted_proxies = _str_tuple("trusted_proxies")

    logger.info(
        API_APP_STARTUP,
        host=host,
        port=port,
        workers=server.workers,
    )

    ws_ping: float | None = (
        server.ws_ping_interval if server.ws_ping_interval > 0 else None
    )
    ws_timeout: float | None = (
        server.ws_ping_timeout if server.ws_ping_timeout > 0 else None
    )

    extra: _OptionalUvicornKwargs = {}
    if ssl_certfile:
        extra["ssl_certfile"] = ssl_certfile
        extra["ssl_keyfile"] = ssl_keyfile
        if ssl_ca_certs:
            extra["ssl_ca_certs"] = ssl_ca_certs
        logger.info(
            API_TLS_CONFIGURED,
            certfile=ssl_certfile,
        )

    if trusted_proxies:
        extra["forwarded_allow_ips"] = ",".join(trusted_proxies)
        extra["proxy_headers"] = True

    # Worker subprocesses and the reloader can only be driven from an
    # import string -- passing a pre-built object makes uvicorn silently
    # ignore ``workers`` / ``reload`` and run a single in-process app. So
    # select the app target by topology: an import-string factory when
    # spawning subprocesses, otherwise the pre-built drain wrapper (which
    # lets the explicit ``config`` flow and keeps unit tests retrieving a
    # raw ``Litestar`` via ``TestClient``). The drain middleware wraps the
    # Litestar app as the outermost ASGI layer in both paths; it
    # intercepts ``lifespan.shutdown`` and runs ``begin_drain`` before
    # forwarding to Litestar, so the per-service ``on_shutdown`` hooks
    # only start once in-flight HTTP traffic has drained.
    needs_import_string = server.workers > 1 or server.reload
    app_target: str | RequestDrainMiddleware = (
        _DRAIN_APP_FACTORY_PATH
        if needs_import_string
        else RequestDrainMiddleware(
            create_app(config=config),
            drain_timeout_seconds=_DRAIN_TIMEOUT_SECONDS,
        )
    )
    uvicorn.run(
        app_target,
        factory=needs_import_string,
        host=host,
        port=port,
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
        **extra,
    )
