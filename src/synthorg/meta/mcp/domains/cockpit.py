"""Mission-control cockpit domain MCP tools.

Read tools expose the live-activity snapshot, the flight-recorder replay, and
the active steering directives; write tools (pause / kill task lifecycle, and
project-scoped steer / steer_supersede) are admin operations guarded by
``require_admin_guardrails`` and route through the same services as the REST
controllers.
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.meta.mcp.domains._cockpit_args import (
    SteerArgs,
    SteerListArgs,
    SteerSupersedeArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_EXECUTION_ID_PROP: dict[str, JsonValue] = {
    "execution_id": {"type": "string", "description": "Execution run identifier"},
}
_TASK_PROP: dict[str, JsonValue] = {
    "task_id": {"type": "string", "description": "Task to act on"},
}
_PROJECT_PROP: dict[str, JsonValue] = {
    "project_id": {"type": "string", "description": "Project the directive targets"},
}
_STEER_PROPS: dict[str, JsonValue] = {
    **_PROJECT_PROP,
    "kind": {
        "type": "string",
        "enum": ["hint", "redirect"],
        "description": "HINT (advisory) or REDIRECT (replan)",
    },
    "text": {"type": "string", "description": "The operator directive text"},
    "narrow_task_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Optional task-id narrowing; empty means project-wide",
    },
    "narrow_agent_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Optional agent-id narrowing; empty means every agent",
    },
    "supersede_task_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tasks to treat as obsolete (EXPLICIT cancels, PROPOSE seeds)",
    },
    "supersede_mode": {
        "type": "string",
        "enum": ["none", "explicit", "propose"],
        "description": "How obsolete tasks are handled",
    },
}
_STEER_SUPERSEDE_PROPS: dict[str, JsonValue] = {
    **_PROJECT_PROP,
    "directive_id": {"type": "string", "description": "Directive to confirm for"},
    "task_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Operator-confirmed obsolete tasks to cancel",
    },
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
        "steer",
        "Issue a project-scoped steering directive (hint or redirect).",
        {**_STEER_PROPS, **ADMIN_GUARDRAIL_PROPERTIES},
        required=("project_id", "kind", "text", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SteerArgs,
    ),
    admin_tool(
        "cockpit",
        "steer_supersede",
        "Confirm the obsolete-task set for a steering directive (cancels them).",
        {**_STEER_SUPERSEDE_PROPS, **ADMIN_GUARDRAIL_PROPERTIES},
        required=(
            "project_id",
            "directive_id",
            "task_ids",
            *ADMIN_GUARDRAIL_REQUIRED,
        ),
        args_model=SteerSupersedeArgs,
    ),
    read_tool(
        "cockpit",
        "steer_list",
        "List the active steering directives for a project.",
        _PROJECT_PROP,
        required=("project_id",),
        args_model=SteerListArgs,
    ),
)
