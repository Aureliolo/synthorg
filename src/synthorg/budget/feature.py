# module-kind: feature
"""Budget feature manifest.

Declares the budget feature's surface: its ``budget`` settings
namespace and the :class:`BudgetStateSlice` (cost-dial services).
Controllers stay hand-wired in ``api/app.py``; this manifest is
declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.budget.state import BudgetStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="budget",
    settings_namespace=SettingNamespace.BUDGET,
    state_slice=BudgetStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "BaselineStore",
        "CoordinationMetricsCollector",
        "CostForecaster",
        "ParetoAnalyzer",
        "StubBenchmarkScoreProvider",
    ),
    depends_on=(),
)
