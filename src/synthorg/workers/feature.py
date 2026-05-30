# module-kind: feature
"""Runtime feature manifest (worker execution + coordination).

Declares the runtime feature's surface: the :class:`RuntimeStateSlice`
(worker execution service, coordinator, distributed task queue +
backend services). The runtime layer has no dedicated settings
namespace. Wiring stays hand-coded at boot via ``runtime_builder``;
this manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.workers._construction import wire_construction
from synthorg.workers.state import RuntimeStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="runtime",
    settings_namespace=None,
    state_slice=RuntimeStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(
        "build_distributed_backend_services",
        "DeadLetterConsumer",
        "SeenClaimsPruner",
        "WorkerHeartbeatSubscriber",
    ),
    depends_on=(),
)
