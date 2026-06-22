"""Security domain MCP tools.

SecOps risk-tier override tools: create / revoke runtime overrides that
reclassify an action type's risk tier (driving the tiered approval-timeout
policy) and list the active overrides. The two mutating tools are
guardrailed admin tools.
"""

from typing import TYPE_CHECKING, get_args

from pydantic import JsonValue

from synthorg.meta.mcp.domains._security_args import (
    RiskOverrideCreateArgs,
    RiskOverrideListArgs,
    RiskOverrideRevokeArgs,
    RiskTierLiteral,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    admin_tool,
    read_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

# Derived from the same ``RiskTierLiteral`` the args model validates against,
# so the advertised MCP schema enum cannot drift from server-side validation.
_TIER_ENUM: list[JsonValue] = list(get_args(RiskTierLiteral))

SECURITY_MCP_TOOLS: tuple[MCPToolDef, ...] = (
    admin_tool(
        "security",
        "risk_override_create",
        "Create a runtime SecOps override reclassifying an action type's risk "
        "tier (requires confirm). The override has a mandatory expiry and takes "
        "effect immediately in the tiered approval-timeout policy.",
        {
            "action_type": {
                "type": "string",
                "description": "The 'category:action' string to reclassify",
            },
            "override_tier": {
                "type": "string",
                "description": "The new risk tier",
                "enum": _TIER_ENUM,
            },
            "expires_at": {
                "type": "string",
                "description": "Override expiry (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=(
            "action_type",
            "override_tier",
            "expires_at",
            *ADMIN_GUARDRAIL_REQUIRED,
        ),
        args_model=RiskOverrideCreateArgs,
    ),
    admin_tool(
        "security",
        "risk_override_revoke",
        "Revoke an active SecOps risk-tier override (requires confirm).",
        {
            "override_id": {
                "type": "string",
                "description": "Identifier of the override to revoke",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("override_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=RiskOverrideRevokeArgs,
    ),
    read_tool(
        "security",
        "risk_override_list",
        "List the currently active SecOps risk-tier overrides.",
        {},
        args_model=RiskOverrideListArgs,
    ),
)
