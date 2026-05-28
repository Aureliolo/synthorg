# module-kind: feature
"""Providers feature manifest.

Declares the providers feature's surface: its ``providers`` settings
namespace, the :class:`ProvidersStateSlice` (registry, router, health
tracker, management / audit / preset-override services), and the
provider REST controller. Wiring stays hand-coded in ``api/app.py``;
this manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.providers import ProviderController
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="providers",
    settings_namespace=SettingNamespace.PROVIDERS,
    state_slice=ProvidersStateSlice,
    controllers=(ProviderController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
