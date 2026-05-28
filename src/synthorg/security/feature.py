# module-kind: feature
"""Security feature manifest.

Declares the security feature's surface: its settings namespace, state slice
(audit log + trust service + autonomy-change strategy), and the audit /
autonomy REST controllers. The services are constructed at app build time;
the feature has no MCP domain or ghost-wired symbols of its own.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.audit import AuditController
from synthorg.api.controllers.autonomy import AutonomyController
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="security",
    settings_namespace=SettingNamespace.SECURITY,
    state_slice=SecurityStateSlice,
    controllers=(AuditController, AutonomyController),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "TrustService",
        "build_autonomy_change_strategy",
        "build_red_team_runtime",
        "RedTeamGateService",
        "AgentEngineRunner",
        "SubmitRedTeamReportTool",
        "InMemoryRedTeamReportRepository",
        "HeuristicGroundingChecker",
        "build_red_team_agent_identity",
    ),
    depends_on=(),
)
