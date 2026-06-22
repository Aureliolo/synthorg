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
from synthorg.integrations.webhooks.activity_service import WebhookActivityService
from synthorg.integrations.webhooks.receipt_service import WebhookReceiptService
from synthorg.integrations.webhooks.replay_protection import ReplayProtector
from synthorg.integrations.webhooks.service import WebhookService
from synthorg.tools.mcp.factory import MCPToolFactory

if TYPE_CHECKING:
    # api.state_slices imports this feature slice; a runtime import here cycles.
    from synthorg.api.state_slices import AppStateSliceMixin


class IntegrationsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the integrations feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connection_catalog: ConnectionCatalog | None = None
    # Always-on credential catalog: the same ConnectionCatalog instance is
    # published here whenever persistence is connected, INDEPENDENT of
    # ``integrations.enabled``. LLM provider authentication resolves
    # credentials through this so it never regresses on a minimal install
    # with the integrations feature off. ``connection_catalog`` above stays
    # gated on ``integrations.enabled`` for the integrations controllers.
    provider_credential_catalog: ConnectionCatalog | None = None
    connection_service: ConnectionService | None = None
    oauth_token_manager: OAuthTokenManager | None = None
    oauth_state_service: OAuthStateService | None = None
    webhook_event_bridge: WebhookEventBridge | None = None
    webhook_service: WebhookService | None = None
    tunnel_provider: TunnelProvider | None = None
    tunnel_service: TunnelService | None = None
    mcp_catalog_service: CatalogService | None = None
    mcp_installations_repo: McpInstallationRepository | None = None
    mcp_bridge_factory: MCPToolFactory | None = None
    health_prober_service: HealthProberService | None = None
    webhook_activity_service: WebhookActivityService | None = None
    webhook_receipt_service: WebhookReceiptService | None = None
    webhook_replay_protector: ReplayProtector | None = None


def connection_catalog_of(app_state: AppStateSliceMixin) -> ConnectionCatalog:
    """Resolve the connection catalog from its slice, or raise 503.

    Returns:
        The wired connection catalog.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).connection_catalog,
        "Connection Catalog",
    )


def provider_credential_catalog_of(
    app_state: AppStateSliceMixin,
) -> ConnectionCatalog | None:
    """Resolve the always-on credential catalog for provider auth.

    Unlike :func:`connection_catalog_of` this does NOT raise when absent:
    LLM provider credential resolution treats a missing catalog as "no
    catalog-backed credentials available" and degrades to its own handling
    (the empty-company / no-persistence path has no provider to authenticate
    anyway). Returns ``None`` when persistence is not connected.

    Returns:
        The wired credential catalog, or ``None``.
    """
    return app_state.slice(IntegrationsStateSlice).provider_credential_catalog


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


def webhook_activity_service_of(
    app_state: AppStateSliceMixin,
) -> WebhookActivityService:
    """Resolve the read-only webhook activity service, or raise 503.

    Returns:
        The wired webhook activity service.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).webhook_activity_service,
        "Webhook Activity Service",
    )


def webhook_receipt_service_of(
    app_state: AppStateSliceMixin,
) -> WebhookReceiptService:
    """Resolve the webhook receipt lifecycle service, or raise 503.

    Returns:
        The wired webhook receipt service.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).webhook_receipt_service,
        "Webhook Receipt Service",
    )


def webhook_replay_protector_of(
    app_state: AppStateSliceMixin,
) -> ReplayProtector:
    """Resolve the singleton webhook replay protector, or raise 503.

    The protector's in-process nonce cache is the source of truth
    between durable-idempotency reads, so a single wired instance must
    serve every request; a per-request build would discard already-seen
    nonces and briefly weaken replay protection.

    Returns:
        The wired webhook replay protector.
    """
    return require_service(
        app_state.slice(IntegrationsStateSlice).webhook_replay_protector,
        "Webhook Replay Protector",
    )
