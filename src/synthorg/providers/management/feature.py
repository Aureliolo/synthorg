# module-kind: feature
"""Model-refresh feature manifest.

Declares the periodic model-refresh / upgrade-recommendation surface:
its :class:`ModelRefreshStateSlice` (refresh service, cadence scheduler,
durable recommendation store) and the :class:`ModelRefreshController`
(recommendation review + manual refresh). The boot hook
``wire_model_refresh`` constructs the slice when the mode is not ``off``;
settings live under the existing ``providers`` namespace.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.model_refresh import ModelRefreshController
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="model_refresh",
    settings_namespace=None,
    state_slice=ModelRefreshStateSlice,
    controllers=(ModelRefreshController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
