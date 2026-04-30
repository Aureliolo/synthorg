"""Quality domain MCP tools.

Covers quality, reviews, and evaluation config versions.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._simple_args import (
    EvaluationVersionsGetArgs,
    EvaluationVersionsListArgs,
    QualityGetAgentQualityArgs,
    QualityGetSummaryArgs,
    QualityListScoresArgs,
    ReviewsCreateArgs,
    ReviewsGetArgs,
    ReviewsListArgs,
    ReviewsUpdateArgs,
)
from synthorg.meta.mcp.tool_builder import PAGINATION_PROPERTIES, read_tool, write_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

QUALITY_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Quality ---
    read_tool(
        "quality",
        "get_summary",
        "Get quality summary for the organization.",
        args_model=QualityGetSummaryArgs,
    ),
    read_tool(
        "quality",
        "get_agent_quality",
        "Get quality metrics for a specific agent.",
        {
            "agent_name": {"type": "string", "description": "Agent name"},
        },
        required=("agent_name",),
        args_model=QualityGetAgentQualityArgs,
    ),
    read_tool(
        "quality",
        "list_scores",
        "List quality score records.",
        {
            "agent_name": {"type": "string", "description": "Filter by agent"},
            **PAGINATION_PROPERTIES,
        },
        args_model=QualityListScoresArgs,
    ),
    # --- Reviews ---
    read_tool(
        "reviews",
        "list",
        "List task reviews.",
        {
            "task_id": {"type": "string", "description": "Filter by task"},
            "reviewer": {"type": "string", "description": "Filter by reviewer"},
            **PAGINATION_PROPERTIES,
        },
        args_model=ReviewsListArgs,
    ),
    read_tool(
        "reviews",
        "get",
        "Get a review by ID.",
        {
            "review_id": {"type": "string", "description": "Review UUID"},
        },
        required=("review_id",),
        args_model=ReviewsGetArgs,
    ),
    write_tool(
        "reviews",
        "create",
        "Create a task review.",
        {
            "task_id": {"type": "string", "description": "Task being reviewed"},
            "score": {
                "type": "number",
                "description": "Review score (0-1)",
                "minimum": 0,
                "maximum": 1,
            },
            "feedback": {"type": "string", "description": "Review feedback"},
        },
        required=("task_id", "score"),
        args_model=ReviewsCreateArgs,
    ),
    write_tool(
        "reviews",
        "update",
        "Update a review.",
        {
            "review_id": {"type": "string", "description": "Review UUID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("review_id", "updates"),
        args_model=ReviewsUpdateArgs,
    ),
    # --- Evaluation config versions ---
    read_tool(
        "evaluation_versions",
        "list",
        "List evaluation config versions.",
        PAGINATION_PROPERTIES,
        args_model=EvaluationVersionsListArgs,
    ),
    read_tool(
        "evaluation_versions",
        "get",
        "Get a specific evaluation config version.",
        {
            "version_num": {
                "type": "integer",
                "description": "Version number",
                "minimum": 1,
            },
        },
        required=("version_num",),
        args_model=EvaluationVersionsGetArgs,
    ),
)
