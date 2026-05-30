# module-kind: feature
"""A2A (agent-to-agent federation) feature manifest.

Declares the A2A feature's surface: its settings namespace, state slice,
and REST controllers. The well-known Agent Card controller mounts at the
application root (``/.well-known``); the JSON-RPC gateway mounts under the
API prefix. Both mount only when their a2a collaborators committed at boot
(predicates read the a2a state slice), preserving the historic
enabled-and-wired gate; the composition root evaluates the predicates at
route assembly.
"""

from synthorg._core.features import (
    ControllerRegistration,
    FeatureManifest,
    FeatureModule,
)
from synthorg.a2a._construction import wire_construction
from synthorg.a2a.gateway import A2AGatewayController
from synthorg.a2a.state import A2aStateSlice
from synthorg.a2a.well_known import WellKnownAgentCardController
from synthorg.api.route_predicates import a2a_gateway_ready, a2a_well_known_ready
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="a2a",
    settings_namespace=SettingNamespace.A2A,
    state_slice=A2aStateSlice,
    controllers=(
        ControllerRegistration(
            controller=WellKnownAgentCardController,
            predicate=a2a_well_known_ready,
            mount="root",
        ),
        ControllerRegistration(
            controller=A2AGatewayController, predicate=a2a_gateway_ready
        ),
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
