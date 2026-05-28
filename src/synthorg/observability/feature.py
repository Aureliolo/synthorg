# module-kind: feature
"""Observability feature manifest.

Declares the observability feature's surface: its settings namespace and
state slice (Prometheus collector + trace handler). The metrics endpoint
and metric hooks are registered as standalone handlers / middleware, and
the collectors are built directly at boot, so the feature has no
controller, MCP domain, or ghost-wired symbols.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="observability",
    settings_namespace=SettingNamespace.OBSERVABILITY,
    state_slice=ObservabilityStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
