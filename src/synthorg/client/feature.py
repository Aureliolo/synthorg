# module-kind: feature
"""Client feature manifest.

Declares the client feature's surface: its ``client`` settings
namespace, the :class:`ClientStateSlice` holding the client-simulation
state, and its REST controllers. The simulation and request controllers
mount unconditionally so that calling them without a wired runtime returns
a clear ``503 Service Unavailable`` (via ``client_simulation_state_of``)
instead of a misleading 404. The ``has_simulation_runtime`` predicate still
drives the ``/capabilities`` flag so the dashboard skips polling a disabled
runtime.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.controllers.clients import ClientController
from synthorg.api.controllers.requests.lifecycle import RequestController
from synthorg.api.controllers.simulations import SimulationController
from synthorg.client._construction import wire_construction
from synthorg.client.state import ClientStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="client",
    settings_namespace=SettingNamespace.CLIENT,
    state_slice=ClientStateSlice,
    controllers=(
        ClientController,
        SimulationController,
        RequestController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=("budget", "engine", "providers"),
)
