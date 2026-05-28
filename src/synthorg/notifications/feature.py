# module-kind: feature
"""Notifications feature manifest.

Declares the notifications feature's surface: its settings namespace and
state slice. The dispatcher is consumed by the lifecycle layer, so the
feature has no REST controller, MCP domain, or ghost-wired symbols.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="notifications",
    settings_namespace=SettingNamespace.NOTIFICATIONS,
    state_slice=NotificationsStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
