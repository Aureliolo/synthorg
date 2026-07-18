# module-kind: orchestrator
"""Boot resolution of the native web-search provider from settings.

Mirrors ``_image_provider_wiring``: reads the ``tools`` namespace web-search
settings, resolves the selected preset and the API key's bound connection, and
builds an :class:`HttpWebSearchProvider` so the ``web_search`` tool and the
research subsystem's web source route through the governed HTTP + SSRF + retry
stack. Fail-open in every failure mode (a misconfigured feature must never
crash the agent runtime), at distinct log levels so a transient resolve failure
is distinguishable from an operator misconfiguration.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.integrations.connections.models import Connection
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.web import WEB_SEARCH_PROVIDER_BOUND
from synthorg.settings.state import config_resolver_of
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.tools.web.providers.presets import get_search_preset

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOLS_NS: str = "tools"


@runtime_checkable
class _ConnectionLookup(Protocol):
    """Minimal seam for resolving a bound connection by name."""

    async def get(self, name: str) -> Connection | None:
        """Return the connection named ``name``, or ``None``."""
        ...


async def _resolve_rate_limiter(
    catalog: _ConnectionLookup,
    connection_name: str,
) -> RateLimiterConfig | None:
    """Return the bound connection's rate-limit config, or ``None``.

    ``None`` lets the provider fall back to the decorator's default ceiling,
    so a runaway agent loop is bounded even when the connection sets no
    explicit limit.
    """
    try:
        conn = await catalog.get(connection_name)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_search",
            context="rate_limiter_resolve",
            note="could not resolve connection rate limiter; using default ceiling",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return conn.rate_limiter if conn is not None else None


async def build_web_search_provider_or_none(
    app_state: AppState,
) -> HttpWebSearchProvider | None:
    """Resolve the boot web-search provider from settings, or ``None``.

    Returns ``None`` (so the ``web_search`` tool is not registered) when the
    feature is disabled, no connection catalog is wired, the selected provider
    is unknown, or no connection is bound. Enabled-but-unbuildable states log
    at ERROR (operator misconfig of a paid capability); a transient
    settings-resolve failure logs at WARNING.

    Returns:
        A bound :class:`HttpWebSearchProvider`, or ``None`` when web search is
        off or unbuildable.
    """
    resolver = config_resolver_of(app_state)
    try:
        enabled = await resolver.get_bool(_TOOLS_NS, "web_search_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_search",
            context="enabled_flag_resolve",
            note="could not resolve tools.web_search_enabled; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not enabled:
        return None

    catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if catalog is None:
        logger.error(
            API_APP_STARTUP,
            service="web_search",
            note="web search enabled but no connection catalog is wired",
        )
        return None

    try:
        provider_id = await resolver.get_str(_TOOLS_NS, "web_search_provider")
        connection_name = await resolver.get_str(_TOOLS_NS, "web_search_connection")
        max_results = await resolver.get_int(_TOOLS_NS, "web_search_max_results")
        timeout = await resolver.get_float(_TOOLS_NS, "web_request_timeout_seconds")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_search",
            context="settings_resolve",
            note="could not resolve web search settings; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    preset = get_search_preset(provider_id)
    if preset is None:
        logger.error(
            API_APP_STARTUP,
            service="web_search",
            note="tools.web_search_provider is not a known provider; tool off",
            provider=provider_id,
        )
        return None
    if not connection_name.strip():
        # An enabled-but-unbound connection is an operator misconfig of a paid
        # capability (the single most likely way this feature ends up silently
        # inert), so it logs at ERROR like the catalog/preset checks above --
        # not WARNING, which alerting that filters on ERROR would miss.
        logger.error(
            API_APP_STARTUP,
            service="web_search",
            note="web search enabled but no connection is bound; tool off",
            provider=provider_id,
        )
        return None

    web = app_state.config.web
    network_policy = (
        web.network_policy
        if web is not None and web.network_policy is not None
        else None
    )
    provider = HttpWebSearchProvider(
        preset=preset,
        catalog=catalog,
        connection_name=connection_name.strip(),
        network_policy=network_policy or NetworkPolicy(),
        timeout_seconds=timeout,
        max_results_ceiling=max_results,
        rate_limiter=await _resolve_rate_limiter(catalog, connection_name.strip()),
        clock=app_state.clock,
    )
    logger.info(
        WEB_SEARCH_PROVIDER_BOUND,
        service="web_search",
        note="wired",
        provider=provider_id,
        connection=connection_name.strip(),
    )
    return provider
