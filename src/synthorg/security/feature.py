# module-kind: feature
"""Security feature manifest.

Declares the security feature's surface: its settings namespace, state slice
(audit log + trust service + autonomy-change strategy), and the audit /
autonomy REST controllers. The services are constructed at app build time. The
feature has no MCP domain, but it does carry ghost-wired symbols for the
red-team runtime and the vision-verifier gate (wired at runtime startup rather
than construction); see ``FEATURE.ghost_wired_symbols`` for the manifest list.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.audit import AuditController
from synthorg.api.controllers.autonomy import AutonomyController
from synthorg.security._construction import wire_construction
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="security",
    settings_namespace=SettingNamespace.SECURITY,
    state_slice=SecurityStateSlice,
    controllers=(AuditController, AutonomyController),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
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
        "build_vision_verifier_gate",
        "DeliverableReviewInputBuilder",
    ),
    depends_on=(),
)
