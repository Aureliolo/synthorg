# module-kind: feature
"""Security feature manifest.

Declares the security feature's surface: its settings namespace, state slice
(audit log + trust service + autonomy-change strategy), and the audit /
autonomy REST controllers. The services are constructed at app build time. The
feature has no MCP domain, but it does carry ghost-wired symbols for the
red-team runtime and the vision-verifier gate (wired at runtime startup rather
than construction); see ``FEATURE.ghost_wired_symbols`` for the manifest list.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.audit import AuditController
from synthorg.api.controllers.autonomy import AutonomyController
from synthorg.api.controllers.risk_overrides import RiskOverrideController
from synthorg.api.controllers.ssrf_violations import SsrfViolationController
from synthorg.meta.mcp.domains.security import SECURITY_MCP_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.security._construction import wire_construction
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace


def _security_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the security MCP handler map.

    Returns:
        The security ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.security import (  # noqa: PLC0415
        SECURITY_HANDLERS,
    )

    return SECURITY_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="security",
    settings_namespace=SettingNamespace.SECURITY,
    state_slice=SecurityStateSlice,
    controllers=(
        AuditController,
        AutonomyController,
        RiskOverrideController,
        SsrfViolationController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="security",
            tool_defs=SECURITY_MCP_TOOLS,
            handlers=_security_mcp_handlers,
        ),
    ),
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
        "KnowledgeSubstrateGroundingChecker",
        "build_red_team_agent_identity",
        "build_vision_verifier_gate",
        "DeliverableReviewInputBuilder",
        "build_policy_engine",
    ),
    depends_on=(),
)
