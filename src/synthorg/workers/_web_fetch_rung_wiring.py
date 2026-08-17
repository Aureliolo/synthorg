# module-kind: orchestrator
"""Boot resolution of the ``web_fetch`` rungs from settings.

Mirrors ``_web_search_provider_wiring``: reads the ``tools`` namespace fetch
settings and assembles the ladder the tool is handed. A rung appears only once
its backing is configured, which is what makes "which rungs exist" the
operator's decision and "which rung serves this call" the agent's.

Fail-open in every failure mode (a misconfigured rung must never crash the
agent runtime), at distinct log levels so a transient resolve failure is
distinguishable from an operator misconfiguration.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.web import WEB_FETCH_PROVIDER_BOUND
from synthorg.settings.state import config_resolver_of
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.providers.fetch_presets import get_fetch_preset
from synthorg.tools.web.providers.http_fetch_provider import HttpWebFetchProvider
from synthorg.tools.web.providers.local_fetch_provider import LocalFetchProvider
from synthorg.tools.web.web_fetch import (
    FetchBackend,
    FetchBudget,
    WebFetchProvider,
    WebFetchRungs,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOLS_NS: str = "tools"


async def build_web_fetch_rungs_or_none(
    app_state: AppState,
) -> WebFetchRungs | None:
    """Assemble the fetch ladder from settings, or ``None`` when off.

    Returns:
        The configured rungs, or ``None`` when the feature is disabled or no
        rung could be built (the tool is then not registered at all, rather
        than registered and unable to answer).
    """
    resolver = config_resolver_of(app_state)
    try:
        enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_fetch",
            context="enabled_flag_resolve",
            note="could not resolve tools.web_fetch_enabled; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not enabled:
        return None

    try:
        char_budget = await resolver.get_int(_TOOLS_NS, "web_fetch_max_characters")
        max_bytes = await resolver.get_int(_TOOLS_NS, "web_fetch_max_response_bytes")
        user_agent = await resolver.get_str(_TOOLS_NS, "web_fetch_user_agent")
        timeout = await resolver.get_float(_TOOLS_NS, "web_request_timeout_seconds")
        proxy_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_proxy_enabled")
        render_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_render_enabled")
        discover = await resolver.get_bool(
            _TOOLS_NS, "web_fetch_docs_index_discovery_enabled"
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_fetch",
            context="settings_resolve",
            note="could not resolve web fetch settings; feature off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    web = app_state.config.web
    network_policy = (
        web.network_policy
        if web is not None and web.network_policy is not None
        else NetworkPolicy()
    )
    # One budget, built once and handed to every rung, so the operator's two
    # ceilings cannot end up applying to one backend and not another.
    budget = FetchBudget(max_response_bytes=max_bytes, char_budget=char_budget)
    providers: dict[FetchBackend, WebFetchProvider] = {
        FetchBackend.LOCAL: LocalFetchProvider(
            network_policy=network_policy,
            budget=budget,
            timeout_seconds=timeout,
            user_agent=user_agent,
        )
    }
    if proxy_enabled:
        proxy = await _build_proxy_rung(
            app_state,
            budget=budget,
            network_policy=network_policy,
            timeout_seconds=timeout,
        )
        if proxy is not None:
            providers[FetchBackend.PROXY] = proxy

    logger.info(
        WEB_FETCH_PROVIDER_BOUND,
        service="web_fetch",
        note="wired",
        backends=sorted(b.value for b in providers),
        render_requested=render_enabled,
    )
    return WebFetchRungs(
        providers=providers,
        discover_docs_index=discover,
        render_enabled=render_enabled,
        char_budget=char_budget,
    )


async def _build_proxy_rung(
    app_state: AppState,
    *,
    budget: FetchBudget,
    network_policy: NetworkPolicy,
    timeout_seconds: float,
) -> HttpWebFetchProvider | None:
    """Build the vendor-reader rung, or ``None`` when it cannot be built.

    Returns:
        The bound provider, or ``None``. Enabled-but-unbuildable logs at ERROR:
        the operator asked for a paid rung and is otherwise never told it is
        absent.
    """
    catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if catalog is None:
        logger.error(
            API_APP_STARTUP,
            service="web_fetch",
            note="proxy backend enabled but no connection catalog is wired",
        )
        return None
    resolver = config_resolver_of(app_state)
    try:
        provider_id = await resolver.get_str(_TOOLS_NS, "web_search_provider")
        connection_name = await resolver.get_str(_TOOLS_NS, "web_search_connection")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="web_fetch",
            context="proxy_settings_resolve",
            note="could not resolve the bound vendor; proxy backend off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    if not provider_id.strip() or not connection_name.strip():
        logger.error(
            API_APP_STARTUP,
            service="web_fetch",
            note=(
                "proxy backend enabled but no search vendor and connection are"
                " bound; it reads the same credential as web_search"
            ),
        )
        return None
    preset = get_fetch_preset(provider_id.strip())
    if preset is None:
        logger.error(
            API_APP_STARTUP,
            service="web_fetch",
            note="the bound search vendor ships no page reader; proxy off",
            provider=provider_id,
        )
        return None
    return HttpWebFetchProvider(
        preset=preset,
        catalog=catalog,
        connection_name=connection_name.strip(),
        budget=budget,
        network_policy=network_policy,
        timeout_seconds=timeout_seconds,
        clock=app_state.clock,
    )


__all__ = ["build_web_fetch_rungs_or_none"]
