# module-kind: code
"""Final Litestar application assembly for the composition root.

Builds the per-operation rate-limit + inflight-concurrency stores and the
:class:`~litestar.Litestar` instance itself (state, CORS, compression, OpenAPI,
middleware, plugins, lifecycle hooks), keeping ``create_app`` a thin
orchestrator that hands over the already-built collaborators.
"""

from collections.abc import Awaitable, Callable

from litestar import Litestar, Router
from litestar.channels import ChannelsPlugin
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin
from litestar.types import Middleware

from synthorg import __version__
from synthorg.api.config import ApiConfig
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.feature_composition import RouteHandlerEntry
from synthorg.api.lifecycle_helpers.boot_resolvers import (
    resolve_api_int,
    resolve_api_str_tuple,
)
from synthorg.api.middleware import security_headers_hook
from synthorg.api.rate_limits import (
    build_inflight_store,
    build_sliding_window_store,
)
from synthorg.api.rate_limits._subject import parse_trusted_networks
from synthorg.api.rate_limits.inflight_protocol import InflightStore
from synthorg.api.rate_limits.protocol import SlidingWindowStore
from synthorg.api.state import AppState

type LifespanHooks = list[Callable[[], Awaitable[None]]]


def build_litestar(  # noqa: PLR0913
    app_state: AppState,
    *,
    api_config: ApiConfig,
    api_router: Router,
    root_handlers: list[RouteHandlerEntry],
    middleware: list[Middleware],
    plugins: list[ChannelsPlugin],
    startup: LifespanHooks,
    shutdown: LifespanHooks,
    skip_lifecycle_shutdown: bool,
) -> Litestar:
    """Build the per-op rate-limit stores and the configured Litestar app.

    Args:
        app_state: The fully-wired application state.
        api_config: The resolved API configuration.
        api_router: The API-prefixed router holding the mounted controllers.
        root_handlers: Root-mounted handlers (e.g. a2a ``/.well-known``).
        middleware: The constructed middleware stack.
        plugins: The Litestar plugins (channels).
        startup: The assembled on-startup lifespan hooks.
        shutdown: The assembled on-shutdown lifespan hooks.
        skip_lifecycle_shutdown: When ``True`` the per-op store close hooks are
            not appended (a shared-app test fixture keeps the stores alive
            across lifespans).

    Returns:
        The configured Litestar application.
    """
    # Per-operation rate limiter. Layered on top of the global two-tier
    # limiter; read from app state by ``per_op_rate_limit`` guards. Built
    # unconditionally so an operator who toggles ``api.per_op_rate_limit_enabled``
    # at runtime does not land on a wired-but-uncapped request path; the
    # config's ``enabled`` flag short-circuits the guard when disabled.
    per_op_rate_limit_store: SlidingWindowStore = build_sliding_window_store(
        api_config.per_op_rate_limit,
    )
    app_state.per_op_limits.set_rate_limit_config(api_config.per_op_rate_limit)
    if not skip_lifecycle_shutdown:
        shutdown = [*shutdown, per_op_rate_limit_store.close]

    # Per-operation inflight-concurrency limiter, enforced by
    # ``PerOpConcurrencyMiddleware``. Built unconditionally (same rationale as
    # the sliding-window store); the middleware short-circuits when
    # ``config.enabled`` is False without ever touching the store.
    per_op_inflight_store: InflightStore = build_inflight_store(
        api_config.per_op_concurrency,
    )
    app_state.per_op_limits.set_concurrency_config(api_config.per_op_concurrency)
    if not skip_lifecycle_shutdown:
        shutdown = [*shutdown, per_op_inflight_store.close]

    trusted_proxies = resolve_api_str_tuple("trusted_proxies")

    return Litestar(
        route_handlers=[api_router, *root_handlers],
        # Disable Litestar's built-in logging config to preserve the structlog
        # multi-file-sink pipeline set up by ``_bootstrap_app_logging``. Without
        # this, Litestar calls dictConfig() at startup which replaces the
        # structlog file sinks with a stdlib queue_listener.
        logging_config=None,
        state=State(
            {
                "app_state": app_state,
                "per_op_rate_limit_store": per_op_rate_limit_store,
                "per_op_rate_limit_config": api_config.per_op_rate_limit,
                "per_op_inflight_store": per_op_inflight_store,
                "per_op_inflight_config": api_config.per_op_concurrency,
                # Mirrors the global limiter's trusted-proxy set so the per-op
                # guard extracts the same "real" client IP behind reverse
                # proxies instead of bucketing all traffic by the proxy's IP.
                "per_op_trusted_proxies": frozenset(trusted_proxies),
                "per_op_trusted_networks": parse_trusted_networks(
                    frozenset(trusted_proxies),
                ),
            },
        ),
        cors_config=CORSConfig(
            allow_origins=list(resolve_api_str_tuple("cors_allowed_origins")),
            allow_methods=list(api_config.cors.allow_methods),  # type: ignore[arg-type]
            allow_headers=list(api_config.cors.allow_headers),
            allow_credentials=api_config.cors.allow_credentials,
        ),
        compression_config=CompressionConfig(
            backend="brotli",
            minimum_size=resolve_api_int("compression_minimum_size_bytes"),
        ),
        # Must be >= artifact API max payload (50 MB) so endpoint-level
        # validation can enforce exact storage limits.
        request_max_body_size=resolve_api_int("request_max_body_size_bytes"),
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
