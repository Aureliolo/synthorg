# module-kind: feature
"""Client feature manifest.

Declares the client feature's surface: its ``client`` settings
namespace, the :class:`ClientStateSlice` holding the client-simulation
state, and its REST controllers. The simulation and request controllers
mount only when the client-simulation runtime is wired (predicate
``has_simulation_runtime``), preserving the historic 404-when-disabled
behaviour; the composition root evaluates the predicate at route
assembly.
"""

from synthorg._core.features import (
    ControllerRegistration,
    FeatureManifest,
    FeatureModule,
)
from synthorg.api.controllers.clients import ClientController
from synthorg.api.controllers.requests import RequestController
from synthorg.api.controllers.simulations import SimulationController
from synthorg.client.state import ClientStateSlice, has_simulation_runtime
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="client",
    settings_namespace=SettingNamespace.CLIENT,
    state_slice=ClientStateSlice,
    controllers=(
        ClientController,
        ControllerRegistration(
            controller=SimulationController, predicate=has_simulation_runtime
        ),
        ControllerRegistration(
            controller=RequestController, predicate=has_simulation_runtime
        ),
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
