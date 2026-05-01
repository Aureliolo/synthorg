"""Analytics event constants."""

from typing import Final

ANALYTICS_TRENDS_QUERIED: Final[str] = "analytics.trends.queried"
ANALYTICS_FORECAST_QUERIED: Final[str] = "analytics.forecast.queried"
ANALYTICS_OVERVIEW_QUERIED: Final[str] = "analytics.overview.queried"

# Per-call analytics layer
ANALYTICS_CALL_METADATA_RECORDED: Final[str] = "analytics.call_metadata_recorded"
ANALYTICS_AGGREGATION_COMPUTED: Final[str] = "analytics.aggregation_computed"
ANALYTICS_RETRY_RATE_ALERT: Final[str] = "analytics.retry_rate_alert"
ANALYTICS_ORCHESTRATION_ALERT: Final[str] = "analytics.orchestration_alert"
ANALYTICS_SERVICE_CREATED: Final[str] = "analytics.service_created"

# Tool: data aggregation queries
ANALYTICS_TOOL_QUERY_START: Final[str] = "analytics.tool.query_start"
ANALYTICS_TOOL_QUERY_SUCCESS: Final[str] = "analytics.tool.query_success"
ANALYTICS_TOOL_QUERY_FAILED: Final[str] = "analytics.tool.query_failed"

# Tool: report generation
ANALYTICS_TOOL_REPORT_START: Final[str] = "analytics.tool.report_start"
ANALYTICS_TOOL_REPORT_SUCCESS: Final[str] = "analytics.tool.report_success"
ANALYTICS_TOOL_REPORT_FAILED: Final[str] = "analytics.tool.report_failed"

# Tool: metric collection
ANALYTICS_TOOL_METRIC_RECORDED: Final[str] = "analytics.tool.metric_recorded"
ANALYTICS_TOOL_METRIC_RECORD_FAILED: Final[str] = "analytics.tool.metric_record_failed"
ANALYTICS_TOOL_METRIC_NOT_ALLOWED: Final[str] = "analytics.tool.metric_not_allowed"

# Tool: provider
ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED: Final[str] = (
    "analytics.tool.provider_not_configured"
)
