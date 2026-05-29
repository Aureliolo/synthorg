# module-kind: feature
"""Budget feature manifest.

Declares the budget feature's surface: its ``budget`` settings
namespace, the :class:`BudgetStateSlice` (cost-dial services), and the
budget / forecast / config-version / reports REST controllers mounted
by the composition root.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.budget import BudgetController
from synthorg.api.controllers.budget_config_versions import (
    BudgetConfigVersionController,
)
from synthorg.api.controllers.budget_forecast import ForecastBudgetController
from synthorg.api.controllers.reports import ReportsController
from synthorg.budget.state import BudgetStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="budget",
    settings_namespace=SettingNamespace.BUDGET,
    state_slice=BudgetStateSlice,
    controllers=(
        BudgetController,
        ForecastBudgetController,
        BudgetConfigVersionController,
        ReportsController,
    ),
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
