# module-kind: feature
"""Integrations feature manifest.

Declares the integrations feature's surface: its ``integrations``
settings namespace, the :class:`IntegrationsStateSlice` (connections,
OAuth, webhooks, tunnel, MCP catalog, health prober), and its REST
controllers. Each controller mounts only when integrations are enabled
and its own collaborators are wired (per-controller readiness
predicates), so a disabled or partially-wired integrations subsystem
404s rather than 503-ing every dashboard poll. The composition root
evaluates the predicates at route assembly.
"""

from synthorg._core.features import (
    ControllerRegistration,
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.controllers.connections import ConnectionsController
from synthorg.api.controllers.integration_health import IntegrationHealthController
from synthorg.api.controllers.mcp_catalog import MCPCatalogController
from synthorg.api.controllers.oauth import OAuthController
from synthorg.api.controllers.tunnel import TunnelController
from synthorg.api.controllers.webhooks.activity import WebhooksActivityController
from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController
from synthorg.api.controllers.webhooks.retry import WebhooksRetryController
from synthorg.api.route_predicates import (
    connections_controller_ready,
    integration_health_controller_ready,
    mcp_catalog_controller_ready,
    oauth_controller_ready,
    tunnel_controller_ready,
    webhooks_controller_ready,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="integrations",
    settings_namespace=SettingNamespace.INTEGRATIONS,
    state_slice=IntegrationsStateSlice,
    controllers=(
        ControllerRegistration(
            controller=ConnectionsController,
            predicate=connections_controller_ready,
        ),
        ControllerRegistration(
            controller=IntegrationHealthController,
            predicate=integration_health_controller_ready,
        ),
        ControllerRegistration(
            controller=OAuthController, predicate=oauth_controller_ready
        ),
        ControllerRegistration(
            controller=WebhooksIngestController,
            predicate=webhooks_controller_ready,
        ),
        ControllerRegistration(
            controller=WebhooksActivityController,
            predicate=webhooks_controller_ready,
        ),
        ControllerRegistration(
            controller=WebhooksRetryController,
            predicate=webhooks_controller_ready,
        ),
        ControllerRegistration(
            controller=MCPCatalogController,
            predicate=mcp_catalog_controller_ready,
        ),
        ControllerRegistration(
            controller=TunnelController, predicate=tunnel_controller_ready
        ),
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
