# module-kind: feature
"""HR feature manifest.

Declares the HR feature's surface: its ``hr`` settings namespace and
the :class:`HrStateSlice` (agent registry, performance, training,
personalities, versions, activity, health, scaling). Controllers stay
hand-wired in ``api/app.py``; this manifest is declarative and feeds
the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.hr.state import HrStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="hr",
    settings_namespace=SettingNamespace.HR,
    state_slice=HrStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
