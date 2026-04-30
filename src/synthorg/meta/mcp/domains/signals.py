"""Signal domain MCP tools.

Defines 9 signal tools as ``MCPToolDef`` instances for the unified
registry, covering org health snapshots, performance, budget,
coordination, scaling, errors, evolution, proposals, and submission.
"""

from typing import TYPE_CHECKING

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
from synthorg.meta.mcp.tool_builder import read_tool, write_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

SIGNAL_MCP_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "signals",
        "get_org_snapshot",
        "Get a complete org-wide signal snapshot combining performance, "
        "budget, coordination, scaling, errors, evolution, and telemetry.",
        {
            "window_days": {
                "type": "integer",
                "description": "Lookback window in days",
                "default": 7,
                "minimum": 1,
            }
        },
        args_model=SignalsGetOrgSnapshotArgs,
    ),
    read_tool(
        "signals",
        "get_performance",
        "Get org-wide performance summary with quality scores, success rates, "
        "collaboration scores, and per-window metrics.",
        {
            "window_days": {
                "type": "integer",
                "description": "Lookback window in days",
                "default": 7,
                "minimum": 1,
            }
        },
        args_model=SignalsGetPerformanceArgs,
    ),
    read_tool(
        "signals",
        "get_budget",
        "Get org-wide budget analytics with spend patterns, category breakdowns, "
        "and exhaustion forecast.",
        args_model=SignalsGetBudgetArgs,
    ),
    read_tool(
        "signals",
        "get_coordination",
        "Get org-wide coordination health metrics including efficiency, overhead, "
        "straggler gaps, and redundancy.",
        args_model=SignalsGetCoordinationArgs,
    ),
    read_tool(
        "signals",
        "get_scaling_history",
        "Get recent scaling decisions and their outcomes "
        "(hired, pruned, deferred, rejected).",
        args_model=SignalsGetScalingHistoryArgs,
    ),
    read_tool(
        "signals",
        "get_error_patterns",
        "Get error taxonomy summary with category distributions and severity trends.",
        args_model=SignalsGetErrorPatternsArgs,
    ),
    read_tool(
        "signals",
        "get_evolution_outcomes",
        "Get recent agent evolution outcomes with proposal approval rates "
        "and adaptation results.",
        args_model=SignalsGetEvolutionOutcomesArgs,
    ),
    read_tool(
        "signals",
        "get_proposals",
        "List improvement proposals by status.",
        {
            "status": {
                "type": "string",
                "description": "Filter by proposal status",
                "enum": ["pending", "approved", "applied", "rolled_back", "regressed"],
            },
        },
        args_model=SignalsGetProposalsArgs,
    ),
    write_tool(
        "signals",
        "submit_proposal",
        "Submit an improvement proposal to the guard chain.",
        {
            "trigger": {
                "type": "string",
                "description": (
                    "What triggered this submission (manual, scheduled, inflection)"
                ),
                "default": "manual",
                "enum": ["manual", "scheduled", "inflection"],
            },
        },
        args_model=SignalsSubmitProposalArgs,
    ),
)
