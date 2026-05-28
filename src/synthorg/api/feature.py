# module-kind: feature
"""Api-core feature manifest.

Declares the cross-cutting API-core surface: the ``api`` settings
namespace and the :class:`ApiCoreStateSlice` that owns services
belonging to no single domain feature (the opaque-pagination cursor
secret today). Controllers stay hand-wired in ``api/app.py``; this
manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="api_core",
    settings_namespace=SettingNamespace.API,
    state_slice=ApiCoreStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=("build_chief_of_staff_proposer",),
    depends_on=(),
)
