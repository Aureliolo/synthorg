"""Task domain MCP tools.

Covers tasks and activities controllers.
"""

from typing import TYPE_CHECKING

from synthorg.core.task_enums import TaskStatus
from synthorg.meta.mcp.domains._tasks_args import (
    ActivitiesListArgs,
    TasksCancelArgs,
    TasksCreateArgs,
    TasksDeleteArgs,
    TasksGetArgs,
    TasksListArgs,
    TasksTransitionArgs,
    TasksUpdateArgs,
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

TASK_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Task CRUD ---
    read_tool(
        "tasks",
        "list",
        "List tasks with optional filtering.",
        {
            "status": {
                "type": "string",
                "description": "Filter by task status",
                "enum": [s.value for s in TaskStatus],
            },
            "assigned_to": {
                "type": "string",
                "description": "Filter by assigned agent",
            },
            "project": {"type": "string", "description": "Filter by project"},
            **PAGINATION_PROPERTIES,
        },
        args_model=TasksListArgs,
    ),
    read_tool(
        "tasks",
        "get",
        "Get a single task by ID.",
        {
            "task_id": {"type": "string", "description": "Task UUID"},
        },
        required=("task_id",),
        args_model=TasksGetArgs,
    ),
    write_tool(
        "tasks",
        "create",
        "Create a new task.",
        {
            "task_data": {
                "type": "object",
                "description": "CreateTaskData payload (title/description/etc.)",
            },
        },
        required=("task_data",),
        args_model=TasksCreateArgs,
    ),
    write_tool(
        "tasks",
        "update",
        "Update an existing task.",
        {
            "task_id": {"type": "string", "description": "Task UUID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("task_id", "updates"),
        args_model=TasksUpdateArgs,
    ),
    admin_tool(
        "tasks",
        "delete",
        "Delete a task (destructive; requires confirm).",
        {
            "task_id": {"type": "string", "description": "Task UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("task_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=TasksDeleteArgs,
    ),
    write_tool(
        "tasks",
        "transition",
        "Transition a task to a new state.",
        {
            "task_id": {"type": "string", "description": "Task UUID"},
            "target_status": {
                "type": "string",
                "description": "Target status",
                "enum": [s.value for s in TaskStatus],
            },
        },
        required=("task_id", "target_status"),
        args_model=TasksTransitionArgs,
    ),
    admin_tool(
        "tasks",
        "cancel",
        "Cancel a task (destructive; requires confirm).",
        {
            "task_id": {"type": "string", "description": "Task UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("task_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=TasksCancelArgs,
    ),
    # --- Activities ---
    read_tool(
        "activities",
        "list",
        "List recent activity events.",
        {
            "project": {"type": "string", "description": "Filter by project"},
            "task_id": {"type": "string", "description": "Filter by task"},
            "window_hours": {
                "type": "integer",
                "description": "Lookback window in hours",
                "minimum": 1,
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=ActivitiesListArgs,
    ),
)
