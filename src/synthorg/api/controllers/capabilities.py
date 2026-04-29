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

if TYPE_CHECKING:
    from synthorg.api.state import AppState


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
        # ``message_bus``); reading the bridge accessor instead would
        # diverge because the bridge requires ``ceremony_scheduler``
        # while the controller does not. ``a2a_peer_registry`` raises
        # 503 when unset, so we read the private slot via ``getattr``
        # with a safe default to avoid the exception inside the
        # response path.
        telemetry_functional = (
            app_state.has_telemetry_collector
            and app_state.telemetry_collector.is_functional
        )
        webhooks_wired = (
            app_state.config.integrations.enabled
            and app_state.has_connection_catalog
            and app_state.has_message_bus
        )
        a2a_wired = getattr(app_state, "_a2a_peer_registry", None) is not None
        return ApiResponse(
            data=CapabilitiesResponse(
                simulations=app_state.has_client_simulation_state,
                requests=app_state.has_client_simulation_state,
                ontology=app_state.has_ontology_service,
                tunnel=app_state.has_tunnel_provider,
                webhooks=webhooks_wired,
                a2a=a2a_wired,
                telemetry=telemetry_functional,
                integrations=app_state.config.integrations.enabled,
            ),
        )


__all__ = [
    "CapabilitiesController",
    "CapabilitiesResponse",
]
