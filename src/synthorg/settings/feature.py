# module-kind: feature
"""Settings feature manifest.

Declares the settings feature's surface: its ``settings`` namespace,
the :class:`SettingsStateSlice` (settings service, read service,
config resolver), and the settings REST controller, mounted by the
discovery-based composition root.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.settings import SettingsController
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="settings",
    settings_namespace=SettingNamespace.SETTINGS,
    state_slice=SettingsStateSlice,
    controllers=(SettingsController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
