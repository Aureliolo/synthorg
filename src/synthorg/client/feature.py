# module-kind: feature
"""Client feature manifest.

Declares the client feature's surface: its ``client`` settings
namespace and the :class:`ClientStateSlice` holding the
client-simulation state. Controllers stay hand-wired in
``api/app.py``; this manifest is declarative and feeds the index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.client.state import ClientStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="client",
    settings_namespace=SettingNamespace.CLIENT,
    state_slice=ClientStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
