# module-kind: feature
"""Settings feature manifest.

Declares the settings feature's surface: its ``settings`` namespace
and the :class:`SettingsStateSlice` (settings service, read service,
config resolver). Controllers stay hand-wired in ``api/app.py``; this
manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="settings",
    settings_namespace=SettingNamespace.SETTINGS,
    state_slice=SettingsStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
