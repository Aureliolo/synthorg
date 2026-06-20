# module-kind: feature
"""Meta feature manifest (self-improvement core).

Declares the meta feature's surface: its ``meta`` settings namespace,
the :class:`MetaStateSlice` (signals, experiments, self-improvement,
reports, analytics, Chief of Staff proposer), its REST controllers
(meta, meta-analytics, analytics, experiments, custom rules), and the
meta + analytics + signals MCP domains mounted by the composition root.
The nested ``meta/charter`` and ``meta/toolsmith`` packages declare
their own manifests.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.analytics.forecast import AnalyticsForecastController
from synthorg.api.controllers.analytics.overview import AnalyticsOverviewController
from synthorg.api.controllers.analytics.trends import AnalyticsTrendsController
from synthorg.api.controllers.conversational import ConversationalController
from synthorg.api.controllers.custom_rules import CustomRuleController
from synthorg.api.controllers.experiments import ExperimentsController
from synthorg.api.controllers.learning import LearningController
from synthorg.api.controllers.meta import MetaController
from synthorg.api.controllers.meta_analytics import MetaAnalyticsController
from synthorg.api.controllers.meta_evolution import MetaEvolutionController
from synthorg.meta.mcp.domains.analytics import ANALYTICS_TOOLS
from synthorg.meta.mcp.domains.meta import META_TOOLS
from synthorg.meta.mcp.domains.signals import SIGNAL_MCP_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingNamespace


def _meta_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the meta MCP handler map.

    Returns:
        The meta ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.meta import META_HANDLERS  # noqa: PLC0415

    return META_HANDLERS


def _analytics_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the analytics MCP handler map.

    Returns:
        The analytics ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.analytics import ANALYTICS_HANDLERS  # noqa: PLC0415

    return ANALYTICS_HANDLERS


def _signals_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the signals MCP handler map.

    Returns:
        The signals ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.signals import SIGNAL_HANDLERS  # noqa: PLC0415

    return SIGNAL_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="meta",
    settings_namespace=SettingNamespace.META,
    state_slice=MetaStateSlice,
    controllers=(
        MetaController,
        MetaEvolutionController,
        ConversationalController,
        MetaAnalyticsController,
        AnalyticsOverviewController,
        AnalyticsTrendsController,
        AnalyticsForecastController,
        ExperimentsController,
        CustomRuleController,
        LearningController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="meta",
            tool_defs=META_TOOLS,
            handlers=_meta_mcp_handlers,
        ),
        mcp_descriptor(
            domain="analytics",
            tool_defs=ANALYTICS_TOOLS,
            handlers=_analytics_mcp_handlers,
        ),
        mcp_descriptor(
            domain="signals",
            tool_defs=SIGNAL_MCP_TOOLS,
            handlers=_signals_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_signals_service",
        "AnalyticsService",
        "ReportsService",
        "OrgInflectionMonitor",
        "build_analytics_collector",
        "SelfImprovementService",
    ),
    depends_on=(),
)
