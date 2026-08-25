# module-kind: feature
"""LLM gateway feature manifest.

Declares the gateway's surface: its state slice (the request pipeline plus
the per-run token signer), its construction wirer (builds them
unconditionally), and the OpenAI-compatible controller. The controller
mounts whenever the pipeline is wired; the ``providers.gateway_enabled``
setting gates behaviour per request, so a disabled gateway 503s rather
than 404s. The gateway settings live in the existing ``providers``
namespace, so the manifest declares no namespace of its own.
"""

from synthorg._core.features import (
    ControllerRegistration,
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.gateway._construction import wire_construction
from synthorg.api.gateway.controller import GatewayController
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.route_predicates import gateway_controller_ready

FEATURE: FeatureModule = FeatureManifest(
    name="llm_gateway",
    settings_namespace=None,
    state_slice=GatewayStateSlice,
    controllers=(
        ControllerRegistration(
            controller=GatewayController,
            predicate=gateway_controller_ready,
        ),
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
