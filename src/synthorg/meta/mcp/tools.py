"""MCP tool definitions for org signal access.

Defines the tool schemas and implementations that the Chief of
Staff agent (and external users) can invoke to query org health
signals. This is the first slice of the broader API-as-MCP vision.
"""

from copy import deepcopy
from typing import TypedDict

from pydantic import JsonValue

from synthorg.observability import get_logger

logger = get_logger(__name__)

# Tool name prefix for all meta signal tools.
TOOL_PREFIX = "synthorg_signals"


class MCPToolDefinitionDict(TypedDict):
    """Wire shape of a single MCP tool definition.

    Mirrors the ``{name, description, parameters}`` triple the MCP
    ``tools/list`` response carries, so consumers read typed fields
    instead of narrowing from ``object``.
    """

    name: str
    description: str
    parameters: dict[str, JsonValue]


# Every windowed signals read accepts the same ``since`` (required) /
# ``until`` (optional, defaults to now) ISO 8601 pair the handlers resolve
# via ``parse_time_window``.
_SINCE_UNTIL_PROPS: dict[str, JsonValue] = {
    "since": {
        "type": "string",
        "description": "Start datetime (ISO 8601, timezone-aware)",
        "format": "date-time",
    },
    "until": {
        "type": "string",
        "description": "End datetime (ISO 8601, timezone-aware); defaults to now",
        "format": "date-time",
    },
}


def _window_params() -> dict[str, JsonValue]:
    """Return a fresh ``since`` / ``until`` parameter schema.

    Returns:
        A standalone parameters object so each tool owns its own dict.
    """
    return {
        "type": "object",
        "properties": dict(_SINCE_UNTIL_PROPS),
        "required": ["since"],
    }


# Tool definitions (name, description, parameter schema).
SIGNAL_TOOLS: tuple[MCPToolDefinitionDict, ...] = (
    {
        "name": f"{TOOL_PREFIX}_get_org_snapshot",
        "description": (
            "Get a complete org-wide signal snapshot combining "
            "performance, budget, coordination, scaling, errors, "
            "evolution, and telemetry summaries."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_performance",
        "description": (
            "Get org-wide performance summary with quality scores, "
            "success rates, collaboration scores, and per-window metrics."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_budget",
        "description": (
            "Get org-wide budget analytics with spend patterns, "
            "category breakdowns, and exhaustion forecast."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_coordination",
        "description": (
            "Get org-wide coordination health metrics including "
            "efficiency, overhead, straggler gaps, and redundancy."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_scaling_history",
        "description": (
            "Get recent scaling decisions and their outcomes "
            "(hired, pruned, deferred, rejected)."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_error_patterns",
        "description": (
            "Get error taxonomy summary with category distributions "
            "and severity trends."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_evolution_outcomes",
        "description": (
            "Get recent agent evolution outcomes with proposal "
            "approval rates and adaptation results."
        ),
        "parameters": _window_params(),
    },
    {
        "name": f"{TOOL_PREFIX}_get_proposals",
        "description": "List improvement proposals by approval status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by approval status",
                    "enum": ["pending", "approved", "rejected", "expired"],
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset",
                    "default": 0,
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Page size",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
    },
    {
        "name": f"{TOOL_PREFIX}_submit_proposal",
        "description": (
            "Submit an improvement proposal to the guard chain. "
            "Used by the Chief of Staff agent to trigger "
            "the improvement cycle (requires confirm)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "description": "ImprovementProposal payload",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be True to confirm the operation",
                },
                "reason": {
                    "type": "string",
                    "description": "Operator-supplied reason for audit trail",
                },
            },
            "required": ["proposal", "confirm", "reason"],
        },
    },
)


def get_tool_definitions() -> tuple[MCPToolDefinitionDict, ...]:
    """Return all MCP tool definitions for the signal server.

    Returns:
        Deep-copied tuple of tool definition dicts.
    """
    return deepcopy(SIGNAL_TOOLS)
