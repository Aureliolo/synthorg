"""Agent domain MCP tools.

Covers the agents and personalities controllers.
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.meta.mcp.domains._agents_args import (
    AgentsCreateArgs,
    AgentsDeleteArgs,
    AgentsGetActivityArgs,
    AgentsGetArgs,
    AgentsGetHealthArgs,
    AgentsGetHistoryArgs,
    AgentsGetPerformanceArgs,
    AgentsListArgs,
    AgentsUpdateArgs,
    AutonomyGetArgs,
    AutonomyUpdateArgs,
    PersonalitiesGetArgs,
    PersonalitiesListArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_AGENT_NAME: dict[str, JsonValue] = {
    "agent_name": {"type": "string", "description": "Agent name"}
}

AGENT_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Agent CRUD ---
    read_tool(
        "agents",
        "list",
        "List all agents with pagination.",
        PAGINATION_PROPERTIES,
        args_model=AgentsListArgs,
    ),
    read_tool(
        "agents",
        "get",
        "Get a single agent by name.",
        _AGENT_NAME,
        required=("agent_name",),
        args_model=AgentsGetArgs,
    ),
    # Admin, not write: this mints a durable organisational principal that
    # holds a role, spends budget and can be selected to judge other agents'
    # work. The ambient write surface every ELEVATED agent carries is the
    # wrong place for that, and its ``delete`` sibling has always agreed.
    admin_tool(
        "agents",
        "create",
        "Create a new agent in the organization (creates a principal; "
        "requires confirm).",
        {
            "identity": {
                "type": "object",
                "description": "AgentIdentity payload",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("identity", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=AgentsCreateArgs,
    ),
    write_tool(
        "agents",
        "update",
        "Update an existing agent.",
        {
            "agent_id": {"type": "string", "description": "Agent ID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("agent_id", "updates"),
        args_model=AgentsUpdateArgs,
    ),
    admin_tool(
        "agents",
        "delete",
        "Remove an agent from the organization (destructive; requires confirm).",
        {**_AGENT_NAME, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("agent_name", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=AgentsDeleteArgs,
    ),
    # --- Agent observability ---
    read_tool(
        "agents",
        "get_performance",
        "Get agent performance summary.",
        _AGENT_NAME,
        required=("agent_name",),
        args_model=AgentsGetPerformanceArgs,
    ),
    read_tool(
        "agents",
        "get_activity",
        "Get agent activity feed.",
        {
            **_AGENT_NAME,
            **PAGINATION_PROPERTIES,
        },
        required=("agent_name",),
        args_model=AgentsGetActivityArgs,
    ),
    read_tool(
        "agents",
        "get_history",
        "Get agent career history.",
        {
            **_AGENT_NAME,
            **PAGINATION_PROPERTIES,
        },
        required=("agent_name",),
        args_model=AgentsGetHistoryArgs,
    ),
    read_tool(
        "agents",
        "get_health",
        "Get agent health status.",
        _AGENT_NAME,
        required=("agent_name",),
        args_model=AgentsGetHealthArgs,
    ),
    # --- Personalities ---
    read_tool(
        "personalities",
        "list",
        "List available personality configurations.",
        PAGINATION_PROPERTIES,
        args_model=PersonalitiesListArgs,
    ),
    read_tool(
        "personalities",
        "get",
        "Get a personality configuration by name.",
        {
            "name": {"type": "string", "description": "Personality name"},
        },
        required=("name",),
        args_model=PersonalitiesGetArgs,
    ),
    # --- Autonomy ---
    read_tool(
        "autonomy",
        "get",
        "Get autonomy level for an agent.",
        {
            "agent_id": {"type": "string", "description": "Agent ID"},
        },
        required=("agent_id",),
        args_model=AutonomyGetArgs,
    ),
    admin_tool(
        "autonomy",
        "update",
        "Update autonomy level for an agent.",
        {
            "agent_id": {"type": "string", "description": "Agent ID"},
            "level": {
                "type": "string",
                "description": "New autonomy level",
                "enum": ["full", "semi", "supervised", "locked"],
            },
            "reason": {
                "type": "string",
                "minLength": 3,
                # The runtime validator on ``AutonomyUpdate.reason``
                # rejects when ``len(reason.strip()) < 3``. Mirror
                # that exactly here: leading/trailing whitespace is
                # allowed, but the trimmed string must start and end
                # with non-whitespace and span at least 3 characters.
                # ``[\s\S]`` (vs ``.``) keeps the pattern correct
                # under JSON Schema's ECMAScript regex semantics
                # (where ``.`` excludes line terminators).
                "pattern": r"^\s*\S[\s\S]{1,}\S\s*$",
                "description": (
                    "Why the change is requested (min 3 chars after strip)"
                ),
            },
        },
        required=("agent_id", "level", "reason"),
        args_model=AutonomyUpdateArgs,
    ),
)
