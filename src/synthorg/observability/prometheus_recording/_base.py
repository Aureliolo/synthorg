# module-kind: declarative
"""Metric-attribute declarations shared by the recording mixins.

``PrometheusCollector.__init__`` populates every attribute; the
family mixins consume them. Declaring them on one base keeps the
metric inventory in a single place.
"""

from prometheus_client import Counter as PromCounter
from prometheus_client import Gauge, Histogram


class _RecordingMetricsBase:
    """Declares the Prometheus metric attributes the mixins consume."""

    # Attributes populated by PrometheusCollector.__init__:
    _security_evaluations: PromCounter
    _provider_tokens: PromCounter
    _provider_cost: PromCounter
    _provider_call_duration: Histogram
    _api_request_duration: Histogram
    _task_runs: PromCounter
    _task_duration: Histogram
    _tool_invocations: PromCounter
    _tool_duration: Histogram
    _provider_errors: PromCounter
    _cache_operations: PromCounter
    _api_error_classification: PromCounter
    _audit_chain_appends: PromCounter
    _audit_chain_depth: Gauge
    _audit_chain_last_append_ts: Gauge
    _otlp_export_batches: PromCounter
    _otlp_export_dropped: PromCounter
    _log_sink_events: PromCounter
    _coordination_efficiency: Gauge
    _coordination_overhead_percent: Gauge
    _escalation_queue_depth: Gauge
    _security_audit_log_fill_ratio: Gauge
    _agent_identity_changes: PromCounter
    _workflow_execution_duration: Histogram
    _client_disconnects: PromCounter
    _approval_decisions: PromCounter
    _autonomy_promotion_decisions: PromCounter
    _escalation_outcomes: PromCounter
    _push_queue_events: PromCounter
    _blueprint_instantiations: PromCounter
    _settings_mutations: PromCounter
    _mcp_handler_outcomes: PromCounter
    _mcp_handler_duration: Histogram
    _budget_query_duration: Histogram
    _audit_chain_verifications: PromCounter


# Push-updated metric names aliased from ``PushMetrics`` onto the
# collector's private ``self._<name>`` attributes in
# ``PrometheusCollector.__init__``. Lives here, next to the attribute
# declarations the aliases populate, so the inventory stays in one place.
_PUSH_ALIASED_METRICS: tuple[str, ...] = (
    "provider_tokens", "provider_cost", "provider_call_duration",
    "api_request_duration", "task_runs",
    "task_duration", "tool_invocations", "tool_duration", "audit_chain_appends",
    "audit_chain_depth", "audit_chain_last_append_ts", "otlp_export_batches",
    "otlp_export_dropped", "log_sink_events", "escalation_queue_depth",
    "security_audit_log_fill_ratio", "agent_identity_changes",
    "workflow_execution_duration", "provider_errors", "cache_operations",
    "api_error_classification", "client_disconnects", "approval_decisions",
    "autonomy_promotion_decisions",
    "escalation_outcomes", "push_queue_events", "blueprint_instantiations",
    "settings_mutations", "mcp_handler_outcomes", "mcp_handler_duration",
    "budget_query_duration", "audit_chain_verifications", "ws_connection_lifetime",
    "ws_revalidation_outcomes", "ws_active_connections", "pg_pool_size",
    "pg_pool_active_connections", "pg_pool_acquire_duration", "pg_pool_exhausted",
)  # fmt: skip
