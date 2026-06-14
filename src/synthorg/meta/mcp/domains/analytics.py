"""Analytics domain MCP tools.

Covers analytics, metrics, and reports controllers.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._simple_args import (
    AnalyticsGetForecastArgs,
    AnalyticsGetOverviewArgs,
    AnalyticsGetTrendsArgs,
    MetricsGetCurrentArgs,
    MetricsGetHistoryArgs,
    ReportsGenerateArgs,
    ReportsGetArgs,
    ReportsListArgs,
)
from synthorg.meta.mcp.tool_builder import (
    PAGINATION_PROPERTIES,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

ANALYTICS_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Analytics ---
    read_tool(
        "analytics",
        "get_overview",
        "Get analytics overview dashboard data.",
        {
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
        },
        required=("since",),
        args_model=AnalyticsGetOverviewArgs,
    ),
    read_tool(
        "analytics",
        "get_trends",
        "Get trend data for metrics over a time window.",
        {
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "metric_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Metrics to analyse (omit for all)",
            },
        },
        required=("since", "until"),
        args_model=AnalyticsGetTrendsArgs,
    ),
    read_tool(
        "analytics",
        "get_forecast",
        "Get forecasted metrics.",
        {
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "horizon_days": {
                "type": "integer",
                "description": "Forecast horizon in days (1-90)",
                "default": 30,
                "minimum": 1,
                "maximum": 90,
            },
        },
        required=("since", "until"),
        args_model=AnalyticsGetForecastArgs,
    ),
    # --- Metrics ---
    read_tool(
        "metrics",
        "get_current",
        "Get current system metrics.",
        {
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "metric_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Metrics to return (omit for all)",
            },
        },
        required=("since",),
        args_model=MetricsGetCurrentArgs,
    ),
    read_tool(
        "metrics",
        "get_history",
        "Get historical metrics.",
        {
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601, timezone-aware)",
                "format": "date-time",
            },
            "metric_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Metrics to sample (non-empty)",
            },
            "sample_count": {
                "type": "integer",
                "description": "Number of evenly-spaced samples (1-100)",
                "default": 8,
                "minimum": 1,
                "maximum": 100,
            },
        },
        required=("since", "until", "metric_names"),
        args_model=MetricsGetHistoryArgs,
    ),
    # --- Reports ---
    read_tool(
        "reports",
        "list",
        "List generated reports.",
        PAGINATION_PROPERTIES,
        args_model=ReportsListArgs,
    ),
    read_tool(
        "reports",
        "get",
        "Get a report by ID.",
        {
            "report_id": {"type": "string", "description": "Report UUID"},
        },
        required=("report_id",),
        args_model=ReportsGetArgs,
    ),
    write_tool(
        "reports",
        "generate",
        "Generate a new report.",
        {
            "template": {
                "type": "string",
                "description": "Report template name",
            },
            "options": {
                "type": "object",
                "description": "Template rendering options",
            },
        },
        required=("template",),
        args_model=ReportsGenerateArgs,
    ),
)
