# module-kind: feature
"""Credentialed-tool MCP gateway feature manifest.

Mounts the credentialed-tool MCP controller, the second surface of the
gateway pair. It carries no state slice of its own: it verifies per-run
bearers with the gateway signer and brokers credentials through the
connection catalog, both owned by other features. It declares
``depends_on=("llm_gateway",)`` so construction is ordered after the shared
``GatewayStateSlice`` (which holds the signer) is wired.
"""

from synthorg._core.features import (
    ControllerRegistration,
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.mcp_gateway.controller import CredentialedMcpController
from synthorg.api.route_predicates import credentialed_mcp_controller_ready

FEATURE: FeatureModule = FeatureManifest(
    name="credentialed_mcp",
    settings_namespace=None,
    state_slice=None,
    controllers=(
        ControllerRegistration(
            controller=CredentialedMcpController,
            predicate=credentialed_mcp_controller_ready,
        ),
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=None,
    ghost_wired_symbols=(),
    depends_on=("llm_gateway",),
)
