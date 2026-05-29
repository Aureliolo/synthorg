# module-kind: feature
"""Meta feature manifest (self-improvement core).

Declares the meta feature's surface: its ``meta`` settings namespace,
the :class:`MetaStateSlice` (signals, experiments, self-improvement,
reports, analytics, Chief of Staff proposer), and its REST controllers
(meta, meta-analytics, analytics, experiments, custom rules) mounted by
the composition root. The nested ``meta/charter`` and ``meta/toolsmith``
packages declare their own manifests.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.analytics import AnalyticsController
from synthorg.api.controllers.custom_rules import CustomRuleController
from synthorg.api.controllers.experiments import ExperimentsController
from synthorg.api.controllers.meta import MetaController
from synthorg.api.controllers.meta_analytics import MetaAnalyticsController
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="meta",
    settings_namespace=SettingNamespace.META,
    state_slice=MetaStateSlice,
    controllers=(
        MetaController,
        MetaAnalyticsController,
        AnalyticsController,
        ExperimentsController,
        CustomRuleController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
