# module-kind: code
"""Cache / OTLP / push-queue / settings / MCP / budget recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.budget import BUDGET_QUERY_OUTCOME
from synthorg.observability.events.mcp import MCP_HANDLER_OUTCOME
from synthorg.observability.prometheus_labels import (
    VALID_BUDGET_QUERY_TYPES,
    VALID_CACHE_NAMES,
    VALID_CACHE_OUTCOMES,
    VALID_LOG_SINK_KINDS,
    VALID_LOG_SINK_OUTCOMES,
    VALID_MCP_HANDLER_OUTCOMES,
    VALID_OTLP_KINDS,
    VALID_OTLP_OUTCOMES,
    VALID_PUSH_QUEUE_OUTCOMES,
    VALID_SETTINGS_NAMESPACES,
    normalize_mcp_tool_label,
    require_label,
    require_non_negative,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _InfraRecordingMixin(_RecordingMetricsBase):
    """Cache / OTLP / push-queue / settings / MCP / budget recording."""

    def record_cache_operation(
        self,
        *,
        cache_name: str,
        outcome: str,
    ) -> None:
        """Increment the in-process cache-operations counter (hit/miss/evict).

        ``cache_name`` bounded by :data:`VALID_CACHE_NAMES`; outcome
        by :data:`VALID_CACHE_OUTCOMES`.  Hit-rate PromQL:
        ``rate(...{outcome="hit"}) / rate(...)`` per ``cache_name``.
        """
        require_label("cache_name", cache_name, VALID_CACHE_NAMES)
        require_label("cache outcome", outcome, VALID_CACHE_OUTCOMES)
        self._cache_operations.labels(
            cache_name=cache_name,
            outcome=outcome,
        ).inc()

    def record_otlp_export(
        self,
        *,
        kind: str,
        outcome: str,
        dropped_records: int = 0,
    ) -> None:
        """Record an OTLP export batch outcome.

        Args:
            kind: ``"logs"`` or ``"traces"``.
            outcome: ``"success"`` or ``"failure"``.
            dropped_records: Count of records dropped (queue full or
                retry budget exhausted). Defaults to 0.

        Raises:
            ValueError: If *kind* or *outcome* are invalid or
                *dropped_records* is negative.
        """
        require_label("OTLP kind", kind, VALID_OTLP_KINDS)
        require_label("OTLP outcome", outcome, VALID_OTLP_OUTCOMES)
        require_non_negative("record_otlp_export: dropped_records", dropped_records)
        self._otlp_export_batches.labels(kind=kind, outcome=outcome).inc()
        if dropped_records > 0:
            self._otlp_export_dropped.labels(kind=kind).inc(dropped_records)

    def record_log_sink_export(
        self,
        *,
        sink: str,
        outcome: str,
    ) -> None:
        """Record a log-shipping sink export outcome.

        Args:
            sink: ``"http"`` or ``"syslog"`` (bounded by
                :data:`VALID_LOG_SINK_KINDS`).
            outcome: ``"success"`` or ``"failure"`` (bounded by
                :data:`VALID_LOG_SINK_OUTCOMES`).

        Raises:
            ValueError: If *sink* or *outcome* are invalid.
        """
        require_label("log sink", sink, VALID_LOG_SINK_KINDS)
        require_label("log sink outcome", outcome, VALID_LOG_SINK_OUTCOMES)
        self._log_sink_events.labels(sink=sink, outcome=outcome).inc()

    def record_push_queue_event(self, *, outcome: str) -> None:
        """Increment the workspace push-queue event counter.

        Args:
            outcome: ``"enqueued"`` when a merge+push is queued, or
                ``"merged"`` when it completes (merge succeeded and, if a
                backend is wired, the default branch was pushed).

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_PUSH_QUEUE_OUTCOMES`.
        """
        require_label("push queue outcome", outcome, VALID_PUSH_QUEUE_OUTCOMES)
        self._push_queue_events.labels(outcome=outcome).inc()

    def record_settings_mutation(self, *, namespace: str) -> None:
        """Increment the settings-mutation counter.

        Args:
            namespace: One of :data:`VALID_SETTINGS_NAMESPACES`
                (mirrors filenames in ``settings/definitions/``).

        Raises:
            ValueError: If *namespace* is not registered.
        """
        require_label("settings namespace", namespace, VALID_SETTINGS_NAMESPACES)
        self._settings_mutations.labels(namespace=namespace).inc()

    def record_mcp_handler_outcome(
        self,
        *,
        tool: str,
        outcome: str,
        duration_sec: float,
    ) -> None:
        """Record an MCP handler invocation's outcome and latency.

        Increments :attr:`_mcp_handler_outcomes` and observes
        :attr:`_mcp_handler_duration` in one call so the two stay in
        lockstep (every observed duration has a matching outcome
        sample). The structured-log mirror at DEBUG carries the same
        kwargs so an offline correlation is possible if Prometheus is
        unavailable.

        ``tool`` is normalised through
        :func:`~synthorg.observability.prometheus_labels.normalize_mcp_tool_label`
        against the MCP registry snapshot seeded at invoker
        construction. Unregistered tool names (e.g. fabricated by a
        misbehaving MCP client to inflate cardinality) are folded to
        :data:`~synthorg.observability.prometheus_labels.MCP_UNKNOWN_TOOL_LABEL`
        before reaching the Prometheus children.

        Args:
            tool: MCP handler tool name (e.g. ``synthorg_messages_get``).
            outcome: One of :data:`VALID_MCP_HANDLER_OUTCOMES`.
            duration_sec: Wall-clock handler duration.

        Raises:
            ValueError: If *outcome* is invalid or *duration_sec* is
                negative.
        """
        require_label("MCP handler outcome", outcome, VALID_MCP_HANDLER_OUTCOMES)
        require_non_negative(
            "record_mcp_handler_outcome: duration_sec",
            duration_sec,
        )
        bounded_tool = normalize_mcp_tool_label(tool)
        self._mcp_handler_outcomes.labels(tool=bounded_tool, outcome=outcome).inc()
        self._mcp_handler_duration.labels(tool=bounded_tool, outcome=outcome).observe(
            duration_sec
        )
        logger.debug(
            MCP_HANDLER_OUTCOME,
            tool=bounded_tool,
            outcome=outcome,
            duration_sec=duration_sec,
        )

    def record_budget_query(
        self,
        *,
        query_type: str,
        duration_sec: float,
    ) -> None:
        """Observe a budget read-path query in the latency histogram.

        Args:
            query_type: One of :data:`VALID_BUDGET_QUERY_TYPES`.
            duration_sec: Wall-clock query duration.

        Raises:
            ValueError: If *query_type* is unknown or
                *duration_sec* is negative.
        """
        require_label("budget query_type", query_type, VALID_BUDGET_QUERY_TYPES)
        require_non_negative("record_budget_query: duration_sec", duration_sec)
        self._budget_query_duration.labels(query_type=query_type).observe(
            duration_sec,
        )
        logger.debug(
            BUDGET_QUERY_OUTCOME,
            query_type=query_type,
            duration_sec=duration_sec,
        )
