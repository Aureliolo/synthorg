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

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.web import WEB_FETCH_PROVIDER_BOUND
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import config_resolver_of
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.fetch_types import (
    FetchBackend,
    FetchBudget,
    WebFetchProvider,
    WebFetchRungs,
)
from synthorg.tools.web.providers.fetch_presets import get_fetch_preset
from synthorg.tools.web.providers.http_fetch_provider import HttpWebFetchProvider
from synthorg.tools.web.providers.local_fetch_provider import LocalFetchProvider

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
    if not await _feature_enabled(resolver):
        return None
    settings = await _resolve_fetch_settings(resolver)
    if settings is None:
        return None

    web = app_state.config.web
    network_policy = (
        web.network_policy
        if web is not None and web.network_policy is not None
        else NetworkPolicy()
    )
    # One budget, built once and handed to every rung, so the operator's two
    # ceilings cannot end up applying to one backend and not another.
    budget = FetchBudget(
        max_response_bytes=settings.max_response_bytes,
        char_budget=settings.char_budget,
    )
    providers: dict[FetchBackend, WebFetchProvider] = {
        FetchBackend.LOCAL: LocalFetchProvider(
            network_policy=network_policy,
            budget=budget,
            timeout_seconds=settings.timeout_seconds,
            user_agent=settings.user_agent,
        )
    }
    if settings.proxy_enabled:
        proxy = await _build_proxy_rung(
            app_state,
            budget=budget,
            network_policy=network_policy,
            timeout_seconds=settings.timeout_seconds,
        )
        if proxy is not None:
            providers[FetchBackend.PROXY] = proxy

    logger.info(
        WEB_FETCH_PROVIDER_BOUND,
        service="web_fetch",
        note="wired",
        backends=sorted(b.value for b in providers),
        render_requested=settings.render_enabled,
    )
    return WebFetchRungs(
        providers=providers,
        discover_docs_index=settings.discover_docs_index,
        render_enabled=settings.render_enabled,
        char_budget=settings.char_budget,
    )


class _FetchSettings(BaseModel):
    """The ``tools`` namespace values one boot of the ladder reads.

    Read as a group because they are resolved as a group: any one of them
    failing means the feature cannot be built, so the ladder is off and the
    caller has nothing to do with a partial answer.

    Attributes:
        char_budget: Markdown ceiling handed to every rung.
        max_response_bytes: Wire ceiling handed to every rung.
        user_agent: What the local rung identifies itself as.
        timeout_seconds: Per-request timeout shared by the rungs.
        proxy_enabled: Whether the operator asked for the vendor reader.
        render_enabled: Whether the operator asked for the rendered rung.
        discover_docs_index: Whether a fetch also probes for ``llms.txt``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    char_budget: int
    max_response_bytes: int
    user_agent: str
    timeout_seconds: float
    proxy_enabled: bool
    render_enabled: bool
    discover_docs_index: bool


async def _feature_enabled(resolver: ConfigResolver) -> bool:
    """Read the master switch.

    Returns:
        Whether the fetch ladder should be built at all. A resolve failure
        reads as off: the alternative is crashing the agent runtime over a
        settings read.
    """
    try:
        return await resolver.get_bool(_TOOLS_NS, "web_fetch_enabled")
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
        return False


async def _resolve_fetch_settings(resolver: ConfigResolver) -> _FetchSettings | None:
    """Read every setting the ladder needs.

    Returns:
        The resolved values, or ``None`` when any read failed.
    """
    try:
        return _FetchSettings(
            char_budget=await resolver.get_int(_TOOLS_NS, "web_fetch_max_characters"),
            max_response_bytes=await resolver.get_int(
                _TOOLS_NS, "web_fetch_max_response_bytes"
            ),
            user_agent=await resolver.get_str(_TOOLS_NS, "web_fetch_user_agent"),
            timeout_seconds=await resolver.get_float(
                _TOOLS_NS, "web_request_timeout_seconds"
            ),
            proxy_enabled=await resolver.get_bool(_TOOLS_NS, "web_fetch_proxy_enabled"),
            render_enabled=await resolver.get_bool(
                _TOOLS_NS, "web_fetch_render_enabled"
            ),
            discover_docs_index=await resolver.get_bool(
                _TOOLS_NS, "web_fetch_docs_index_discovery_enabled"
            ),
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
