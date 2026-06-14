"""Signal domain MCP tools.

Defines 9 signal tools as ``MCPToolDef`` instances for the unified
registry, covering org health snapshots, performance, budget,
coordination, scaling, errors, evolution, proposals, and submission.
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.meta.mcp.domains._simple_args import (
    SignalsGetBudgetArgs,
    SignalsGetCoordinationArgs,
    SignalsGetErrorPatternsArgs,
    SignalsGetEvolutionOutcomesArgs,
    SignalsGetOrgSnapshotArgs,
    SignalsGetPerformanceArgs,
    SignalsGetProposalsArgs,
    SignalsGetScalingHistoryArgs,
    SignalsSubmitProposalArgs,
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

# Every windowed signals read threads the same ``since`` (required) /
# ``until`` (optional, defaults to now) ISO 8601 pair the handlers
# resolve via ``parse_time_window``.
_SINCE_UNTIL_PROPERTIES: dict[str, JsonValue] = {
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

SIGNAL_MCP_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "signals",
        "get_org_snapshot",
        "Get a complete org-wide signal snapshot combining performance, "
        "budget, coordination, scaling, errors, evolution, and telemetry.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetOrgSnapshotArgs,
    ),
    read_tool(
        "signals",
        "get_performance",
        "Get org-wide performance summary with quality scores, success rates, "
        "collaboration scores, and per-window metrics.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetPerformanceArgs,
    ),
    read_tool(
        "signals",
        "get_budget",
        "Get org-wide budget analytics with spend patterns, category breakdowns, "
        "and exhaustion forecast.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetBudgetArgs,
    ),
    read_tool(
        "signals",
        "get_coordination",
        "Get org-wide coordination health metrics including efficiency, overhead, "
        "straggler gaps, and redundancy.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetCoordinationArgs,
    ),
    read_tool(
        "signals",
        "get_scaling_history",
        "Get recent scaling decisions and their outcomes "
        "(hired, pruned, deferred, rejected).",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetScalingHistoryArgs,
    ),
    read_tool(
        "signals",
        "get_error_patterns",
        "Get error taxonomy summary with category distributions and severity trends.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetErrorPatternsArgs,
    ),
    read_tool(
        "signals",
        "get_evolution_outcomes",
        "Get recent agent evolution outcomes with proposal approval rates "
        "and adaptation results.",
        _SINCE_UNTIL_PROPERTIES,
        required=("since",),
        args_model=SignalsGetEvolutionOutcomesArgs,
    ),
    read_tool(
        "signals",
        "get_proposals",
        "List improvement proposals by approval status.",
        {
            "status": {
                "type": "string",
                "description": "Filter by approval status",
                "enum": ["pending", "approved", "rejected", "expired"],
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=SignalsGetProposalsArgs,
    ),
    admin_tool(
        "signals",
        "submit_proposal",
        "Submit an improvement proposal to the guard chain (requires confirm).",
        {
            "proposal": {
                "type": "object",
                "description": "ImprovementProposal payload",
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("proposal", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SignalsSubmitProposalArgs,
    ),
)
