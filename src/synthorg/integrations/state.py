"""Integrations feature state slice.

Holds the external-integration services: the connection catalog +
service, OAuth token manager + state service, the workflow webhook
event bridge + webhook service, the tunnel provider + service, the MCP
catalog service + installations repo, and the health prober. The
catalog / token manager / tunnel provider / webhook bridge / MCP
catalog are constructor-injected; the rest are wired lazily. All
fields are ``None`` until wired; readers guard accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.engine.workflow.webhook_bridge import (
    WebhookEventBridge,
)
from synthorg.integrations.connections.catalog import (
    ConnectionCatalog,
)
from synthorg.integrations.connections.mcp_service import (
    ConnectionService,
)
from synthorg.integrations.health.prober import (
    HealthProberService,
)
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallationRepository,
)
from synthorg.integrations.mcp_catalog.service import CatalogService
from synthorg.integrations.oauth.state_service import (
    OAuthStateService,
)
from synthorg.integrations.oauth.token_manager import (
    OAuthTokenManager,
)
from synthorg.integrations.tunnel.mcp_service import TunnelService
from synthorg.integrations.tunnel.protocol import TunnelProvider
from synthorg.integrations.webhooks.service import WebhookService

if TYPE_CHECKING:
    # api.state_slices imports this feature slice; a runtime import here cycles.
    from synthorg.api.state_slices import AppStateSliceMixin


class IntegrationsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the integrations feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_catalog: ConnectionCatalog | None = None
    connection_service: ConnectionService | None = None
    oauth_token_manager: OAuthTokenManager | None = None
    oauth_state_service: OAuthStateService | None = None
    webhook_event_bridge: WebhookEventBridge | None = None
    webhook_service: WebhookService | None = None
    tunnel_provider: TunnelProvider | None = None
    tunnel_service: TunnelService | None = None
    mcp_catalog_service: CatalogService | None = None
    mcp_installations_repo: McpInstallationRepository | None = None
    health_prober_service: HealthProberService | None = None


def connection_catalog_of(app_state: AppStateSliceMixin) -> ConnectionCatalog:
    """Resolve the connection catalog from its slice, or raise 503.

    Returns:
        The wired connection catalog.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).connection_catalog,
        "Connection Catalog",
    )


def connection_service_of(app_state: AppStateSliceMixin) -> ConnectionService:
    """Resolve the connection service from its slice, or raise 503.

    Returns:
        The wired connection service.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).connection_service,
        "Connection Service",
    )


def tunnel_service_of(app_state: AppStateSliceMixin) -> TunnelService:
    """Resolve the tunnel service from its slice, or raise 503.

    Returns:
        The wired tunnel service.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).tunnel_service, "Tunnel Service"
    )


def webhook_service_of(app_state: AppStateSliceMixin) -> WebhookService:
    """Resolve the webhook service from its slice, or raise 503.

    Returns:
        The wired webhook service.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).webhook_service, "Webhook Service"
    )
