# module-kind: code
"""Integrations feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.integrations.state import IntegrationsStateSlice

if TYPE_CHECKING:
    # api.* eagerly imports the integrations slice this module wires; a
    # runtime import of api.construction_wiring / api.state forms a cycle.
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the integrations slice from the auto-wired bundle."""
    integrations = deps.integrations
    app_state.swap_slice(
        IntegrationsStateSlice.model_construct(
            connection_catalog=integrations.connection_catalog,
            provider_credential_catalog=integrations.provider_credential_catalog,
            oauth_token_manager=integrations.oauth_token_manager,
            health_prober_service=integrations.health_prober_service,
            tunnel_provider=integrations.tunnel_provider,
            webhook_event_bridge=integrations.webhook_event_bridge,
            mcp_catalog_service=integrations.mcp_catalog_service,
            mcp_installations_repo=integrations.mcp_installations_repo,
        )
    )
