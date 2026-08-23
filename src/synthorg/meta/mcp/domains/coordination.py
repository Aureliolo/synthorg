"""Coordination domain MCP tools.

Covers coordination and coordination metrics.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._simple_args import (
    CoordinationGetTaskMetricsArgs,
    CoordinationMetricsListArgs,
)
from synthorg.meta.mcp.tool_builder import PAGINATION_PROPERTIES, read_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

COORDINATION_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Task coordination metrics (read-only lookup) ---
    read_tool(
        "coordination",
        "get_task_metrics",
        (
            "Return the most recent coordination metrics record for a task. "
            "This is a read-only lookup over the metrics store; triggering "
            "coordination is owned by the engine loop and exposed via the "
            "REST endpoint, not MCP."
        ),
        {
            "task_id": {"type": "string", "description": "Task UUID"},
        },
        required=("task_id",),
        args_model=CoordinationGetTaskMetricsArgs,
    ),
    # --- Coordination metrics ---
    read_tool(
        "coordination_metrics",
        "list",
        "List coordination metric records.",
        {
            "task_id": {"type": "string", "description": "Filter by task"},
            "agent_id": {"type": "string", "description": "Filter by agent"},
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601)",
                "format": "date-time",
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=CoordinationMetricsListArgs,
    ),
)
