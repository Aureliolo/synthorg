"""Chief of Staff role definition.

Defines the default Chief of Staff role that can be optionally
added to a company configuration. The role has access to all
signal MCP tools and can submit improvement proposals.
"""

from synthorg.meta.mcp.tools import TOOL_PREFIX

# Role metadata.
ROLE_NAME = "Chief of Staff"
ROLE_DEPARTMENT = "Executive"
ROLE_DESCRIPTION = (
    "Meta-analyst responsible for observing organizational "
    "signals, identifying improvement opportunities, and "
    "proposing changes to the company configuration, structure, "
    "and agent policies."
)

# Skills required for the Chief of Staff role.
REQUIRED_SKILLS = (
    "organizational_analysis",
    "strategic_planning",
    "data_interpretation",
    "pattern_recognition",
)

# MCP tools the Chief of Staff has access to (legacy name-based).
TOOL_ACCESS = (
    f"{TOOL_PREFIX}_get_org_snapshot",
    f"{TOOL_PREFIX}_get_performance",
    f"{TOOL_PREFIX}_get_budget",
    f"{TOOL_PREFIX}_get_coordination",
    f"{TOOL_PREFIX}_get_scaling_history",
    f"{TOOL_PREFIX}_get_error_patterns",
    f"{TOOL_PREFIX}_get_evolution_outcomes",
    f"{TOOL_PREFIX}_get_proposals",
    f"{TOOL_PREFIX}_submit_proposal",
)

# MCP capability patterns for the unified synthorg-api server.
# These are used by MCPToolScoper to filter the full tool catalog
# to only the tools the Chief of Staff needs.
MCP_CAPABILITIES = (
    "signals:*",
    "analytics:read",
    "agents:read",
    "coordination:read",
    "budget:read",
    "quality:read",
    "meta:*",
)


def get_role_definition() -> dict[str, object]:
    """Return the Chief of Staff role definition.

    This can be used to register the role in the company config
    as a CustomRole or as a built-in default role.

    Returns:
        Role definition dict.
    """
    return {
        "name": ROLE_NAME,
        "department": ROLE_DEPARTMENT,
        "description": ROLE_DESCRIPTION,
        "required_skills": REQUIRED_SKILLS,
        "reports_to": "CEO",
        "tool_access": TOOL_ACCESS,
        "mcp_capabilities": MCP_CAPABILITIES,
        "system_prompt_template": None,  # Uses prompts.py templates.
    }
