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
        args_model=AnalyticsGetOverviewArgs,
    ),
    read_tool(
        "analytics",
        "get_trends",
        "Get trend data for a metric over time.",
        {
            "period": {
                "type": "string",
                "description": "Time period (daily, weekly, monthly)",
            },
            "metric": {"type": "string", "description": "Metric to analyze"},
        },
        args_model=AnalyticsGetTrendsArgs,
    ),
    read_tool(
        "analytics",
        "get_forecast",
        "Get forecasted metrics.",
        {
            "horizon_days": {
                "type": "integer",
                "description": "Forecast horizon in days (1-90)",
                "default": 30,
                "minimum": 1,
                "maximum": 90,
            },
        },
        args_model=AnalyticsGetForecastArgs,
    ),
    # --- Metrics ---
    read_tool(
        "metrics",
        "get_current",
        "Get current system metrics.",
        args_model=MetricsGetCurrentArgs,
    ),
    read_tool(
        "metrics",
        "get_history",
        "Get historical metrics.",
        {
            "metric_name": {"type": "string", "description": "Metric name"},
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
        },
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
            "report_type": {
                "type": "string",
                "description": "Type of report to generate",
            },
            "parameters": {"type": "object", "description": "Report parameters"},
        },
        required=("report_type",),
        args_model=ReportsGenerateArgs,
    ),
)
