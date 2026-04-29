"""Capabilities controller -- runtime feature discovery for the dashboard.

Returns one boolean per optional subsystem so the web dashboard can
gate polling on the surfaces that are actually wired in this
deployment. Without this surface the dashboard polled every endpoint
unconditionally and recorded a 503 every cycle for any subsystem the
operator had not configured -- 16+ such errors logged in 57h of
runtime against a minimally-configured install (issue #1666 B-3).
"""

from typing import TYPE_CHECKING

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.api.state import AppState

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
        tunnel: Tunnel provider is configured (pyngrok + auth token).
        webhooks: Webhook event bridge is configured.
        a2a: A2A peer registry / client are configured.
        telemetry: Anonymous Logfire telemetry is enabled and
            functional (an enabled-but-degraded reporter still
            reports False; the dashboard only surfaces telemetry UI
            when the reporter actually delivers).
        integrations: The integrations subsystem
            (``effective_config.integrations.enabled``) is on; when
            False the connections / oauth / webhooks / mcp catalog
            surfaces are not registered at all.
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


class CapabilitiesController(Controller):
    """Runtime feature-flag introspection for the web dashboard."""

    path = "/capabilities"
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
        """Build the capabilities snapshot from the live app state."""
        app_state: AppState = state.app_state
        # Reading directly from the live ``has_*`` predicates so the
        # source of truth is the app state's wiring decisions rather
        # than a parallel boolean cache that could drift. ``webhooks``
        # and ``a2a`` use the nullable accessors directly because the
        # state mixin returns ``None`` (webhook bridge) or raises
        # 503 (a2a) on absence; ``is not None`` covers both shapes
        # without a try/except.
        telemetry_functional = (
            app_state.has_telemetry_collector
            and app_state.telemetry_collector.is_functional
        )
        webhook_bridge = app_state.webhook_event_bridge
        a2a_enabled = app_state.config.a2a.enabled
        return ApiResponse(
            data=CapabilitiesResponse(
                simulations=app_state.has_client_simulation_state,
                requests=app_state.has_client_simulation_state,
                ontology=app_state.has_ontology_service,
                tunnel=app_state.has_tunnel_provider,
                webhooks=webhook_bridge is not None,
                a2a=a2a_enabled,
                telemetry=telemetry_functional,
                integrations=app_state.config.integrations.enabled,
            ),
        )


__all__ = [
    "CapabilitiesController",
    "CapabilitiesResponse",
]
