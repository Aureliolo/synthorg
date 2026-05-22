"""Mission-control cockpit domain MCP tools.

Read tools expose the live-activity snapshot and the flight-recorder
replay; intervention tools (pause / kill / hint / redirect) are admin
operations guarded by ``require_admin_guardrails`` and route through the
same services as the REST controller.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_EXECUTION_ID_PROP = {
    "execution_id": {"type": "string", "description": "Execution run identifier"},
}
_AGENT_TEXT_PROPS = {
    "execution_id": {"type": "string", "description": "Execution run identifier"},
    "agent_id": {"type": "string", "description": "Agent to steer"},
    "text": {"type": "string", "description": "Operator hint / redirect text"},
}
_TASK_PROP = {
    "task_id": {"type": "string", "description": "Task to act on"},
}

COCKPIT_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "cockpit",
        "get_live_activity",
        "Get the live org-activity snapshot (who/what + stuck/runaway flags).",
    ),
    read_tool(
        "cockpit",
        "get_flight_recorder_frames",
        "Get the flight-recorder timeline (newest-first) for an execution.",
        {**_EXECUTION_ID_PROP, **PAGINATION_PROPERTIES},
        required=("execution_id",),
    ),
    read_tool(
        "cockpit",
        "seek_flight_recorder",
        "Reconstruct flight-recorder scrubber state at a target turn.",
        {
            **_EXECUTION_ID_PROP,
            "turn_index": {
                "type": "integer",
                "description": "1-based target turn index",
                "minimum": 1,
            },
        },
        required=("execution_id", "turn_index"),
    ),
    admin_tool(
        "cockpit",
        "intervene_pause",
        "Pause a running task (transition to INTERRUPTED).",
        {**_TASK_PROP, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("task_id", *ADMIN_GUARDRAIL_REQUIRED),
    ),
    admin_tool(
        "cockpit",
        "intervene_kill",
        "Kill a running task (cancel it).",
        {**_TASK_PROP, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("task_id", *ADMIN_GUARDRAIL_REQUIRED),
    ),
    admin_tool(
        "cockpit",
        "intervene_hint",
        "Queue a hint for a running agent (applied at the next safe boundary).",
        {**_AGENT_TEXT_PROPS, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("execution_id", "agent_id", "text", *ADMIN_GUARDRAIL_REQUIRED),
    ),
    admin_tool(
        "cockpit",
        "intervene_redirect",
        "Queue a redirect for a running agent.",
        {**_AGENT_TEXT_PROPS, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("execution_id", "agent_id", "text", *ADMIN_GUARDRAIL_REQUIRED),
    ),
)
