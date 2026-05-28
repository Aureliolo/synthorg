# module-kind: feature
"""Coordination feature manifest.

Declares the coordination feature's surface: its settings namespace and
state slice (the coordination-metrics store). The store is constructed at
app build time; the feature has no MCP domain or ghost-wired symbols here.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.coordination_metrics import (
    CoordinationMetricsController,
)
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="coordination",
    settings_namespace=SettingNamespace.COORDINATION,
    state_slice=CoordinationStateSlice,
    controllers=(CoordinationMetricsController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
