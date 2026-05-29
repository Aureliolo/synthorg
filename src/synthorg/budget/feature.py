# module-kind: feature
"""Budget feature manifest.

Declares the budget feature's surface: its ``budget`` settings
namespace, the :class:`BudgetStateSlice` (cost-dial services), the
budget / forecast / config-version / reports REST controllers, and the
budget MCP domain mounted by the composition root.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.budget import BudgetController
from synthorg.api.controllers.budget_config_versions import (
    BudgetConfigVersionController,
)
from synthorg.api.controllers.budget_forecast import ForecastBudgetController
from synthorg.api.controllers.reports import ReportsController
from synthorg.budget.state import BudgetStateSlice
from synthorg.meta.mcp.domains.budget import BUDGET_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _budget_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the budget MCP handler map.

    Returns:
        The budget ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.budget import BUDGET_HANDLERS  # noqa: PLC0415

    return BUDGET_HANDLERS


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
    mcp_handlers=(
        mcp_descriptor(
            domain="budget",
            tool_defs=BUDGET_TOOLS,
            handlers=_budget_mcp_handlers,
        ),
    ),
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
