"""Analytics event constants."""

from typing import Final

ANALYTICS_TRENDS_QUERIED: Final[str] = "analytics.trends.queried"
ANALYTICS_FORECAST_QUERIED: Final[str] = "analytics.forecast.queried"
ANALYTICS_OVERVIEW_QUERIED: Final[str] = "analytics.overview.queried"
ANALYTICS_OVERVIEW_TREND_SOURCE_DEGRADED: Final[str] = (
    "analytics.overview.trend_source_degraded"
)
ANALYTICS_OVERVIEW_OUTCOME_DEGRADED: Final[str] = "analytics.overview.outcome_degraded"
ANALYTICS_TASK_LIST_COLLECTED: Final[str] = "analytics.task_list.collected"

# Per-call analytics layer
ANALYTICS_CALL_METADATA_RECORDED: Final[str] = "analytics.call_metadata_recorded"
ANALYTICS_AGGREGATION_COMPUTED: Final[str] = "analytics.aggregation_computed"
ANALYTICS_BREAKDOWN_COMPUTED: Final[str] = "analytics.breakdown_computed"
ANALYTICS_BREAKDOWN_MIXED_CURRENCY: Final[str] = "analytics.breakdown.mixed_currency"
ANALYTICS_RETRY_RATE_ALERT: Final[str] = "analytics.retry_rate_alert"
ANALYTICS_RETRY_ALERT_DISPATCH_FAILED: Final[str] = (
    "analytics.retry_alert.dispatch_failed"
)
ANALYTICS_ORCHESTRATION_ALERT: Final[str] = "analytics.orchestration_alert"
ANALYTICS_PROMPT_CLASS_COST_ALERT: Final[str] = "analytics.prompt_class.cost_alert"
ANALYTICS_PROMPT_CLASS_LATENCY_ALERT: Final[str] = (
    "analytics.prompt_class.latency_alert"
)
ANALYTICS_PROMPT_CLASS_ALERT_DISPATCH_FAILED: Final[str] = (
    "analytics.prompt_class.alert_dispatch_failed"
)
ANALYTICS_CAPABILITY_LOOKUP_FAILED: Final[str] = "analytics.capability_lookup_failed"
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

# Tool: provider config
ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED: Final[str] = (
    "analytics.tool.provider_not_configured"
)
