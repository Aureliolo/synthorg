"""MCP tool definitions for org signal access.

Defines the tool schemas and implementations that the Chief of
Staff agent (and external users) can invoke to query org health
signals. This is the first slice of the broader API-as-MCP vision.
"""

from copy import deepcopy

from synthorg.observability import get_logger

logger = get_logger(__name__)

# Tool name prefix for all meta signal tools.
TOOL_PREFIX = "synthorg_signals"

# Tool definitions (name, description, parameter schema).
SIGNAL_TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": f"{TOOL_PREFIX}_get_org_snapshot",
        "description": (
            "Get a complete org-wide signal snapshot combining "
            "performance, budget, coordination, scaling, errors, "
            "evolution, and telemetry summaries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "window_days": {
                    "type": "integer",
                    "description": "Lookback window in days (default 7)",
                    "default": 7,
                },
            },
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_performance",
        "description": (
            "Get org-wide performance summary with quality scores, "
            "success rates, collaboration scores, and per-window metrics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "window_days": {
                    "type": "integer",
                    "description": "Lookback window in days (default 7)",
                    "default": 7,
                },
            },
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_budget",
        "description": (
            "Get org-wide budget analytics with spend patterns, "
            "category breakdowns, and exhaustion forecast."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_coordination",
        "description": (
            "Get org-wide coordination health metrics including "
            "efficiency, overhead, straggler gaps, and redundancy."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_scaling_history",
        "description": (
            "Get recent scaling decisions and their outcomes "
            "(hired, pruned, deferred, rejected)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_error_patterns",
        "description": (
            "Get error taxonomy summary with category distributions "
            "and severity trends."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_evolution_outcomes",
        "description": (
            "Get recent agent evolution outcomes with proposal "
            "approval rates and adaptation results."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": f"{TOOL_PREFIX}_get_proposals",
        "description": (
            "List improvement proposals by status "
            "(pending, applied, rolled_back, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by proposal status",
                    "enum": [
                        "pending",
                        "approved",
                        "applied",
                        "rolled_back",
                        "regressed",
                    ],
                },
            },
        },
    },
    {
        "name": f"{TOOL_PREFIX}_submit_proposal",
        "description": (
            "Submit an improvement proposal to the guard chain. "
            "Used by the Chief of Staff agent to trigger "
            "the improvement cycle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "trigger": {
                    "type": "string",
                    "description": (
                        "What triggered this submission (manual, scheduled, inflection)"
                    ),
                    "default": "manual",
                },
            },
        },
    },
)


def get_tool_definitions() -> tuple[dict[str, object], ...]:
    """Return all MCP tool definitions for the signal server.

    Returns:
        Deep-copied tuple of tool definition dicts.
    """
    return deepcopy(SIGNAL_TOOLS)
