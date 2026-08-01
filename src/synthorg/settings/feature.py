# module-kind: feature
"""Settings feature manifest.

Declares the settings feature's surface: its ``settings`` namespace,
the :class:`SettingsStateSlice` (settings service, read service,
config resolver), and the per-sub-domain settings REST controllers
(core CRUD + schema, observability sinks, security export/import),
mounted by the discovery-based composition root.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.settings.core import SettingsCoreController
from synthorg.api.controllers.settings.observability import (
    SettingsObservabilityController,
)
from synthorg.api.controllers.settings.security import SettingsSecurityController
from synthorg.settings._construction import wire_construction
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="settings",
    settings_namespace=SettingNamespace.SETTINGS,
    state_slice=SettingsStateSlice,
    controllers=(
        SettingsCoreController,
        SettingsObservabilityController,
        SettingsSecurityController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
