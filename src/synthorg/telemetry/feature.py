# module-kind: feature
"""Telemetry feature manifest.

Declares the telemetry feature's surface: its settings namespace and state
slice. The collector is read by the shared health / capabilities
controllers rather than a telemetry-owned controller, and it has no MCP
domain or ghost-wired symbols (it is constructed directly at boot).
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.settings.enums import SettingNamespace
from synthorg.telemetry.state import TelemetryStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="telemetry",
    settings_namespace=SettingNamespace.TELEMETRY,
    state_slice=TelemetryStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
