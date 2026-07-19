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

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.analytics.forecast import AnalyticsForecastController
from synthorg.api.controllers.analytics.overview import AnalyticsOverviewController
from synthorg.api.controllers.analytics.trends import AnalyticsTrendsController
from synthorg.api.controllers.conversation_history import (
    ConversationHistoryController,
)
from synthorg.api.controllers.custom_rules import CustomRuleController
from synthorg.api.controllers.experiments import ExperimentsController
from synthorg.api.controllers.learning import LearningController
from synthorg.api.controllers.meta import MetaController
from synthorg.api.controllers.meta_alerts import MetaAlertsController
from synthorg.api.controllers.meta_analytics import MetaAnalyticsController
from synthorg.api.controllers.meta_evolution import MetaEvolutionController
from synthorg.api.controllers.turn import TurnController
from synthorg.meta._mcp_loaders import (
    load_analytics_mcp_handlers,
    load_meta_mcp_handlers,
    load_signals_mcp_handlers,
)
from synthorg.meta.mcp.domains.analytics import ANALYTICS_TOOLS
from synthorg.meta.mcp.domains.meta import META_TOOLS
from synthorg.meta.mcp.domains.signals import SIGNAL_MCP_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="meta",
    settings_namespace=SettingNamespace.META,
    state_slice=MetaStateSlice,
    controllers=(
        MetaController,
        MetaEvolutionController,
        MetaAlertsController,
        TurnController,
        ConversationHistoryController,
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
            handlers=load_meta_mcp_handlers,
        ),
        mcp_descriptor(
            domain="analytics",
            tool_defs=ANALYTICS_TOOLS,
            handlers=load_analytics_mcp_handlers,
        ),
        mcp_descriptor(
            domain="signals",
            tool_defs=SIGNAL_MCP_TOOLS,
            handlers=load_signals_mcp_handlers,
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
        "build_rollback_executor",
        "_wire_alert_repo",
        "SQLiteAlertRepository",
        "PostgresAlertRepository",
        "PersistentAlertSink",
        "resolve_chat_answer",
        "ToolApprovalConsumer",
    ),
    depends_on=(),
)
