# module-kind: feature
"""Meta feature manifest (self-improvement core).

Declares the meta feature's surface: its ``meta`` settings namespace
and the :class:`MetaStateSlice` (signals, experiments, self-improvement,
reports, analytics, Chief of Staff proposer). The nested ``meta/charter``
and ``meta/toolsmith`` packages declare their own manifests. Controllers
stay hand-wired in ``api/app.py``; this manifest is declarative and
feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="meta",
    settings_namespace=SettingNamespace.META,
    state_slice=MetaStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
