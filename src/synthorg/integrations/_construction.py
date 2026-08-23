# module-kind: code
"""Integrations feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.core.domain_errors import ServiceUnavailableError

# Runtime import (not TYPE_CHECKING): the catalog-source closure below
# annotates its return type with ConnectionCatalog, and typeguard
# resolves that annotation at runtime when the manager's bind_runtime
# checks the callable argument (PEP 649 __annotate__).
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.mcp_service import ConnectionService
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.integrations.tunnel.manager import TunnelManager
from synthorg.integrations.tunnel.mcp_service import TunnelService

if TYPE_CHECKING:
    # api.* eagerly imports the integrations slice this module wires; a
    # runtime import of api.construction_wiring / api.state forms a cycle.
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the integrations slice from the auto-wired bundle."""
    integrations = deps.integrations
    tunnel = integrations.tunnel_provider
    if tunnel is not None:
        _bind_tunnel_runtime(app_state, tunnel)
    app_state.swap_slice(
        IntegrationsStateSlice.model_construct(
            connection_catalog=integrations.connection_catalog,
            provider_credential_catalog=integrations.provider_credential_catalog,
            # The MCP connection facade wraps the enabled-gated catalog, so it
            # wires only when the integrations connection surface is present.
            connection_service=(
                ConnectionService(catalog=integrations.connection_catalog)
                if integrations.connection_catalog is not None
                else None
            ),
            secret_capture_service=integrations.secret_capture_service,
            oauth_token_manager=integrations.oauth_token_manager,
            health_prober_service=integrations.health_prober_service,
            tunnel_provider=tunnel,
            tunnel_service=TunnelService(provider=tunnel)
            if tunnel is not None
            else None,
            inbound_thread_registry=integrations.inbound_thread_registry,
            mcp_catalog_service=integrations.mcp_catalog_service,
            mcp_installations_repo=integrations.mcp_installations_repo,
        )
    )


def _bind_tunnel_runtime(app_state: AppState, manager: TunnelManager) -> None:
    """Bind live settings + catalog lookups into the tunnel manager.

    Both closures read the current app state on every call, so they
    keep working across the two-phase boot (settings and persistence
    come up after construction) and any later runtime re-wiring.
    """

    async def _selected_provider() -> str | None:
        # Deferred: ``settings.state`` pulls heavy hubs into the
        # cold-import graph; the lookup only runs at request time.
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        try:
            resolver = config_resolver_of(app_state)
        except ServiceUnavailableError:
            # Settings not wired yet (early boot / no persistence);
            # the manager falls back to its default provider.
            return None
        return await resolver.get_str("integrations", "tunnel_provider")

    def _credential_catalog() -> ConnectionCatalog | None:
        return app_state.slice(IntegrationsStateSlice).provider_credential_catalog

    manager.bind_runtime(
        selection_source=_selected_provider,
        catalog_source=_credential_catalog,
    )
