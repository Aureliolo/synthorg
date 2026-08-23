"""Capabilities controller -- runtime feature discovery for the dashboard.

Returns one boolean per optional subsystem so the web dashboard can
gate polling on the surfaces that are actually wired in this
deployment. Without it the dashboard polls every endpoint
unconditionally and records a 503 each cycle for every subsystem the
operator has not configured, which on a minimally-configured install
is a steady stream of errors describing nothing wrong.
"""

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict

from synthorg.a2a.state import A2aStateSlice
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState
from synthorg.client.state import has_simulation_runtime
from synthorg.communication.state import CommunicationStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger
from synthorg.ontology.state import OntologyStateSlice
from synthorg.settings.state import config_resolver_of
from synthorg.telemetry.state import TelemetryStateSlice
from synthorg.tools.state import ToolsStateSlice
from synthorg.tools.web.readiness import (
    WebSearchBlocker,
    resolve_web_research_readiness,
)

logger = get_logger(__name__)


class CapabilitiesResponse(BaseModel):
    """Boolean flags describing which optional subsystems are wired.

    Every flag here corresponds to either a controller that is only
    registered when the underlying service is configured (so the
    route returns 404 when the flag is False) or a controller whose
    handlers degrade to ``ServiceUnavailableError`` (503) when the
    matching app-state attribute is absent. The dashboard reads this
    once per session and skips polling endpoints whose flag is False.

    Attributes:
        simulations: Client simulation runtime is configured.
        requests: Request facade (depends on client simulation state)
            is configured.
        ontology: Ontology service is configured.
        tunnel: External tunnel provider is configured.
        webhooks: WebhooksController is registered. Mirrors the
            create_app() readiness predicate
            (``integrations.enabled`` AND ``connection_catalog`` AND
            ``message_bus``) so the flag reports the route, not a
            sibling auto-wired bridge that follows a different gate.
        a2a: A2A peer registry / client are configured.
        telemetry: Anonymous product telemetry is enabled and
            functional (an enabled-but-degraded reporter still
            reports False; the dashboard only surfaces telemetry UI
            when the reporter actually delivers).
        integrations: The integrations subsystem
            (``effective_config.integrations.enabled``) is on; when
            False the connections / oauth / webhooks / mcp catalog
            surfaces are not registered at all.
        web_search: A search provider is selected AND bound, so agents
            can actually search. False covers both "off by choice" and
            "on but unusable", which ``web_search_blocker``
            distinguishes.
        web_search_blocker: The named condition stopping web search, or
            ``none``. Typed as the enum so the published schema keeps the
            closed set rather than degrading to a free-form string. Anything
            other than ``none`` / ``disabled`` is an operator misconfiguration
            the dashboard surfaces, because a feature that reads as enabled
            everywhere else and answers nothing is otherwise invisible until
            an agent needs it.
        web_search_message: Operator-facing explanation of the blocker,
            empty when there is nothing to fix.
        web_search_notify: Whether the dashboard should raise the blocker
            with the operator, which a dismissal turns off.
        web_search_reusable_connections: Saved connections whose vendor
            matches the selected provider, so a blocked setup can point at
            a credential that already exists instead of asking again.
        web_fetch: Agents can read a page as markdown.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    simulations: bool
    requests: bool
    ontology: bool
    tunnel: bool
    webhooks: bool
    a2a: bool
    telemetry: bool
    integrations: bool
    web_search: bool
    web_search_blocker: WebSearchBlocker
    web_search_message: str
    web_search_notify: bool
    web_search_reusable_connections: tuple[str, ...]
    web_fetch: bool


class CapabilitiesController(Controller):
    """Runtime feature-flag introspection for the web dashboard."""

    path = "/capabilities"
    tags = ("capabilities",)
    guards = [require_read_access]  # noqa: RUF012

    @get(
        "/",
        summary="Report which optional subsystems are wired",
        description=(
            "Returns one boolean per optional subsystem. The dashboard "
            "uses this to gate polling on the surfaces actually wired "
            "in this deployment, avoiding 503-spam against optional "
            "services that are intentionally not configured."
        ),
    )
    async def get_capabilities(
        self,
        state: State,
    ) -> ApiResponse[CapabilitiesResponse]:
        """Build the capabilities snapshot from the live app state.

        Returns:
            ``ApiResponse[CapabilitiesResponse]`` instance.
        """
        app_state: AppState = state.app_state
        # Read directly from the live runtime-wiring signals so each
        # flag reflects what's actually plumbed, not what the config
        # asked for. ``a2a`` checks the actual peer registry (the
        # collaborator the gateway routes through) instead of
        # ``config.a2a.enabled``; if auto-wire silently failed (e.g.
        # missing ``connection_catalog``), the config flag would still
        # be ``True`` while the subsystem is unavailable, breaking the
        # capability-gating contract. ``webhooks`` mirrors the
        # WebhooksController readiness predicate in ``create_app()``
        # (``integrations.enabled`` AND ``connection_catalog`` AND
        # ``message_bus``). The A2A peer registry is read
        # through its slice (``None`` until the gateway is wired) to
        # avoid the 503 the bridge accessor raises inside the
        # response path.
        telemetry_collector = app_state.slice(TelemetryStateSlice).collector
        telemetry_functional = (
            telemetry_collector is not None and telemetry_collector.is_functional
        )
        webhooks_wired = (
            app_state.config.integrations.enabled
            and app_state.slice(IntegrationsStateSlice).connection_catalog is not None
            and app_state.slice(CommunicationStateSlice).message_bus is not None
        )
        a2a_wired = app_state.slice(A2aStateSlice).peer_registry is not None
        simulation_runtime = has_simulation_runtime(app_state)
        # Two different questions, and the capability is the AND of them.
        # Readiness answers "is this configured", read live from the same
        # settings boot builds from, and it owns the blocker an operator has to
        # act on. What the runtime INSTALLED answers "can an agent call it":
        # assembly stops before the tool registry when no provider is active or
        # the decomposition pair is unbound, and neither is visible to the
        # settings the readiness verdict reads. Reporting readiness alone told
        # an operator web research was on while no session held either tool.
        readiness = await resolve_web_research_readiness(
            config_resolver_of(app_state),
            connections=app_state.slice(IntegrationsStateSlice).connection_catalog,
        )
        installed = app_state.slice(ToolsStateSlice).web_research
        return ApiResponse(
            data=CapabilitiesResponse(
                simulations=simulation_runtime,
                requests=simulation_runtime,
                ontology=app_state.slice(OntologyStateSlice).service is not None,
                tunnel=app_state.slice(IntegrationsStateSlice).tunnel_provider
                is not None,
                webhooks=webhooks_wired,
                a2a=a2a_wired,
                telemetry=telemetry_functional,
                integrations=app_state.config.integrations.enabled,
                web_search=readiness.search_ready
                and installed is not None
                and installed.search,
                web_search_blocker=readiness.search_blocker,
                web_search_message=readiness.describe(),
                web_search_notify=readiness.should_notify,
                web_search_reusable_connections=readiness.reusable_connections,
                web_fetch=readiness.fetch_enabled
                and installed is not None
                and installed.fetch,
            ),
        )


__all__ = [
    "CapabilitiesController",
    "CapabilitiesResponse",
]
