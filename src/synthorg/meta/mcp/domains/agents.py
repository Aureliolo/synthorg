"""Agent domain MCP tools.

Covers agents, personalities, and training controllers.
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
    CollaborationGetCalibrationArgs,
    CollaborationGetScoreArgs,
    PersonalitiesGetArgs,
    PersonalitiesListArgs,
    TrainingGetSessionArgs,
    TrainingListSessionsArgs,
    TrainingStartSessionArgs,
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
    write_tool(
        "agents",
        "create",
        "Create a new agent in the organization.",
        {
            "name": {"type": "string", "description": "Agent name"},
            "role": {"type": "string", "description": "Agent role"},
            "department": {"type": "string", "description": "Department name"},
        },
        required=("name", "role", "department"),
        args_model=AgentsCreateArgs,
    ),
    write_tool(
        "agents",
        "update",
        "Update an existing agent.",
        {
            "agent_name": {"type": "string", "description": "Agent name"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("agent_name", "updates"),
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
        _AGENT_NAME,
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
    # --- Training ---
    read_tool(
        "training",
        "list_sessions",
        "List training sessions.",
        PAGINATION_PROPERTIES,
        args_model=TrainingListSessionsArgs,
    ),
    read_tool(
        "training",
        "get_session",
        "Get a training session by ID.",
        {
            "session_id": {"type": "string", "description": "Training session ID"},
        },
        required=("session_id",),
        args_model=TrainingGetSessionArgs,
    ),
    write_tool(
        "training",
        "start_session",
        "Start a new training session for an agent.",
        {
            "new_agent_id": {
                "type": "string",
                "description": "ID of the agent being trained",
            },
            "new_agent_role": {
                "type": "string",
                "description": "Role of the new hire",
            },
            "new_agent_level": {
                "type": "string",
                "description": "Seniority level of the new hire",
                "enum": ["junior", "mid", "senior"],
            },
            "new_agent_department": {
                "type": "string",
                "description": "Department of the new hire (optional)",
            },
            "enabled_content_types": {
                "type": "array",
                "description": (
                    "Content extractors to run (optional; defaults to all). "
                    "Valid values: procedural, semantic, tool_patterns."
                ),
                "items": {
                    "type": "string",
                    "enum": ["procedural", "semantic", "tool_patterns"],
                },
            },
        },
        required=("new_agent_id", "new_agent_role", "new_agent_level"),
        args_model=TrainingStartSessionArgs,
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
    # --- Collaboration ---
    read_tool(
        "collaboration",
        "get_score",
        "Get collaboration score for an agent.",
        {
            "agent_id": {"type": "string", "description": "Agent ID"},
        },
        required=("agent_id",),
        args_model=CollaborationGetScoreArgs,
    ),
    read_tool(
        "collaboration",
        "get_calibration",
        "Get collaboration calibration data.",
        {
            "agent_id": {"type": "string", "description": "Agent ID"},
        },
        required=("agent_id",),
        args_model=CollaborationGetCalibrationArgs,
    ),
)
