"""Push-time recording methods for :class:`PrometheusCollector`.

Extracted from :mod:`synthorg.observability.prometheus_collector` to
keep that module under the 800-line ceiling mandated by ``CLAUDE.md``.
The :class:`RecordingMixin` declares every ``record_*`` push method;
``PrometheusCollector`` inherits the mixin so the public API is
unchanged for callers (``collector.record_task_run(...)`` still works
without import changes).

No business logic about scrape flow, registry refresh, or service
wiring lives here; that stays in :class:`PrometheusCollector`. The
mixin only references attributes set up in the collector's
``__init__`` (``self._task_runs``, ``self._task_duration``, ...).
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from prometheus_client import Counter as PromCounter
    from prometheus_client import Gauge, Histogram
from synthorg.observability.events.blueprint import BLUEPRINT_INSTANTIATE_OUTCOME
from synthorg.observability.events.budget import BUDGET_QUERY_OUTCOME
from synthorg.observability.events.mcp import MCP_HANDLER_OUTCOME
from synthorg.observability.events.metrics import (
    API_REQUEST_VALIDATION_FAILED,
    CLIENT_DISCONNECTED,
    METRICS_COORDINATION_RECORDED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.events.security import SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME
from synthorg.observability.prometheus_labels import (
    VALID_API_ERROR_CATEGORIES,
    VALID_APPROVAL_OUTCOMES,
    VALID_AUDIT_APPEND_STATUSES,
    VALID_AUDIT_VERIFICATION_OUTCOMES,
    VALID_BLUEPRINT_OUTCOMES,
    VALID_BUDGET_QUERY_TYPES,
    VALID_CACHE_NAMES,
    VALID_CACHE_OUTCOMES,
    VALID_DISCONNECT_REASONS,
    VALID_DISCONNECT_TRANSPORTS,
    VALID_ESCALATION_OUTCOMES,
    VALID_IDENTITY_CHANGE_TYPES,
    VALID_MCP_HANDLER_OUTCOMES,
    VALID_OTLP_KINDS,
    VALID_OTLP_OUTCOMES,
    VALID_PG_BACKENDS,
    VALID_PROVIDER_ERROR_CLASSES,
    VALID_SETTINGS_NAMESPACES,
    VALID_STATUS_CLASSES,
    VALID_TASK_OUTCOMES,
    VALID_TOOL_OUTCOMES,
    VALID_VERDICTS,
    VALID_WORKFLOW_EXECUTION_STATUSES,
    VALID_WS_REVALIDATION_OUTCOMES,
    VALID_WS_TRANSPORTS,
    normalize_mcp_tool_label,
    require_finite,
    require_label,
    require_non_negative,
    status_class,
    validate_agent_id,
    validate_department,
    validate_tool_name,
    validate_workflow_definition_id,
)

logger = get_logger(__name__)


class RecordingMixin:
    """Push-time recording methods for the Prometheus collector.

    The mixin declares the metric attributes it consumes as class-level
    annotations so static checkers (mypy) understand they are populated
    by ``PrometheusCollector.__init__``.
    """

    # Attributes populated by PrometheusCollector.__init__:
    _security_evaluations: PromCounter
    _provider_tokens: PromCounter
    _provider_cost: PromCounter
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
    _coordination_efficiency: Gauge
    _coordination_overhead_percent: Gauge
    _escalation_queue_depth: Gauge
    _security_audit_log_fill_ratio: Gauge
    _agent_identity_changes: PromCounter
    _workflow_execution_duration: Histogram
    _client_disconnects: PromCounter
    _approval_decisions: PromCounter
    _escalation_outcomes: PromCounter
    _blueprint_instantiations: PromCounter
    _settings_mutations: PromCounter
    _mcp_handler_outcomes: PromCounter
    _mcp_handler_duration: Histogram
    _budget_query_duration: Histogram
    _audit_chain_verifications: PromCounter
    _ws_connection_lifetime: Histogram
    _ws_revalidation_outcomes: PromCounter
    _ws_active_connections: Gauge
    _pg_pool_size: Gauge
    _pg_pool_active_connections: Gauge
    _pg_pool_acquire_duration: Histogram
    _pg_pool_exhausted: PromCounter

    def record_security_verdict(self, verdict: str) -> None:
        """Increment the security verdict counter.

        Called by a thin hook around ``SecOpsService.evaluate_pre_tool()``.

        Args:
            verdict: The verdict string -- one of ``"allow"``,
                ``"deny"``, ``"escalate"``, or ``"output_scan"``
                (see :data:`VALID_VERDICTS`).

        Raises:
            ValueError: If *verdict* is not in the allowed set.
        """
        require_label("security verdict", verdict, VALID_VERDICTS)
        self._security_evaluations.labels(verdict=verdict).inc()

    def record_provider_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record an LLM provider call's token and cost usage.

        Called from ``integration/provider_caller.py`` after a
        completion resolves (after retry/rate-limit). Tokens and cost
        are monotonically increasing counters -- never reset at
        runtime.

        Args:
            provider: Provider id (e.g. ``"example-provider"``).
            model: Model name (e.g. ``"large"``).
            input_tokens: Tokens in the request prompt.
            output_tokens: Tokens in the response completion.
            cost: Computed cost in the configured currency for this call.
        """
        require_non_negative("record_provider_usage: input_tokens", input_tokens)
        require_non_negative("record_provider_usage: output_tokens", output_tokens)
        require_non_negative("record_provider_usage: cost", cost)
        self._provider_tokens.labels(
            provider=provider,
            model=model,
            direction="input",
        ).inc(input_tokens)
        self._provider_tokens.labels(
            provider=provider,
            model=model,
            direction="output",
        ).inc(output_tokens)
        self._provider_cost.labels(
            provider=provider,
            model=model,
        ).inc(cost)

    def record_api_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_sec: float,
    ) -> None:
        """Record an HTTP request handler's duration.

        Called from ``RequestLoggingMiddleware`` (``api/middleware.py``)
        once the response is fully constructed. ``route`` is a route
        template (e.g. ``"/agents/{agent_id}"``), never a raw path;
        the middleware resolves this via ``scope["route_handler"]``.

        Args:
            method: HTTP method (uppercase, e.g. ``"GET"``).
            route: Route template string; ``"__unmatched__"`` for 404s.
            status_code: Response status code (100-599).
            duration_sec: Wall-clock duration in seconds.
        """
        sc = status_class(status_code)
        if sc not in VALID_STATUS_CLASSES:
            logger.warning(
                API_REQUEST_VALIDATION_FAILED,
                component="api_request",
                reason="invalid_status_code",
                method=method,
                route=route,
                status_code=status_code,
            )
            msg = f"record_api_request: invalid status_code {status_code!r}"
            raise ValueError(msg)
        require_non_negative("record_api_request: duration_sec", duration_sec)
        self._api_request_duration.labels(
            method=method,
            route=route,
            status_class=sc,
        ).observe(duration_sec)

    def record_task_run(
        self,
        *,
        outcome: str,
        duration_sec: float | None,
    ) -> None:
        """Record a task's final outcome and runtime.

        Args:
            outcome: One of ``"succeeded"``, ``"failed"``,
                ``"cancelled"``, ``"rejected"``.
            duration_sec: Wall-clock duration in seconds, or
                ``None`` if the engine has no recorded creation
                timestamp (e.g. a task created before the current
                process restart). The outcome counter increments
                in either case; the duration histogram observation
                is skipped when ``duration_sec is None`` so an
                untimed task does not skew the distribution with a
                spurious 0-duration sample.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        require_label("task outcome", outcome, VALID_TASK_OUTCOMES)
        self._task_runs.labels(outcome=outcome).inc()
        if duration_sec is not None:
            require_non_negative("record_task_run: duration_sec", duration_sec)
            self._task_duration.labels(outcome=outcome).observe(duration_sec)

    def record_tool_invocation(
        self,
        *,
        tool_name: str,
        outcome: str,
        duration_sec: float,
    ) -> None:
        """Record a tool invocation's outcome and runtime.

        Args:
            tool_name: Registered tool name (e.g. ``"web_search"``).
            outcome: One of ``"success"``, ``"error"``, ``"timeout"``.
            duration_sec: Wall-clock duration in seconds.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        # tool_name is bounded against the running ToolRegistry's
        # snapshot; fabricated names are rejected at push time so
        # cardinality cannot grow beyond the registry's size.
        validate_tool_name(tool_name)
        require_label("tool outcome", outcome, VALID_TOOL_OUTCOMES)
        require_non_negative("record_tool_invocation: duration_sec", duration_sec)
        self._tool_invocations.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).inc()
        self._tool_duration.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).observe(duration_sec)

    def record_provider_error(
        self,
        *,
        provider: str,
        model: str,
        error_class: str,
    ) -> None:
        """Increment the provider-error counter for a failed completion.

        Wired from :meth:`BaseCompletionProvider.complete`/``stream``;
        the caller classifies the exception via
        :func:`synthorg.providers.errors.classify_provider_error` so
        ``error_class`` stays bounded.
        """
        require_label("error_class", error_class, VALID_PROVIDER_ERROR_CLASSES)
        self._provider_errors.labels(
            provider=provider,
            model=model,
            error_class=error_class,
        ).inc()

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

    def record_api_error(
        self,
        *,
        category: str,
        status_code: int,
    ) -> None:
        """Increment the API error classification counter (4xx/5xx only).

        ``category`` tracks the RFC 9457 taxonomy
        (:data:`VALID_API_ERROR_CATEGORIES`, mirroring
        :class:`synthorg.api.errors.ErrorCategory`); 2xx/3xx status
        codes are rejected so the counter only covers real failures.
        """
        require_label("api error category", category, VALID_API_ERROR_CATEGORIES)
        sc = status_class(status_code)
        if sc not in {"4xx", "5xx"}:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="api_error",
                reason="non_error_status_code",
                category=category,
                status_code=status_code,
                mapped_class=sc,
            )
            msg = (
                f"record_api_error: status_code {status_code!r} is not 4xx/5xx"
                f" (mapped to {sc!r})"
            )
            raise ValueError(msg)
        self._api_error_classification.labels(
            category=category,
            status_class=sc,
        ).inc()

    def record_audit_append(
        self,
        *,
        status: str,
        chain_depth: int,
        timestamp_unix: float,
    ) -> None:
        """Record an audit chain append event.

        Args:
            status: One of ``"signed"`` (TSA granted), ``"fallback"``
                (local clock), or ``"error"``.
            chain_depth: Hash chain length after the append.
            timestamp_unix: Unix epoch seconds of the append.

        Raises:
            ValueError: If *status* is not a valid value or
                *chain_depth* is negative.
        """
        require_label("audit append status", status, VALID_AUDIT_APPEND_STATUSES)
        require_non_negative("record_audit_append: chain_depth", chain_depth)
        require_finite("record_audit_append: timestamp_unix", timestamp_unix)
        self._audit_chain_appends.labels(status=status).inc()
        self._audit_chain_depth.set(chain_depth)
        self._audit_chain_last_append_ts.set(timestamp_unix)

    def record_security_audit_fill_ratio(self, *, ratio: float) -> None:
        """Set the security audit log occupancy fraction.

        Args:
            ratio: ``len(_entries) / _max_entries`` of the
                ``AuditLog``; bounded in ``[0.0, 1.0]`` so a value
                near ``1.0`` signals imminent eviction.

        Raises:
            ValueError: If *ratio* is non-finite or outside [0.0, 1.0].
        """
        require_finite("record_security_audit_fill_ratio: ratio", ratio)
        if not 0.0 <= ratio <= 1.0:
            msg = f"ratio must be in [0.0, 1.0], got {ratio}"
            raise ValueError(msg)
        self._security_audit_log_fill_ratio.set(ratio)

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

    def record_coordination_metrics(
        self,
        *,
        efficiency: float,
        overhead_percent: float,
    ) -> None:
        """Update coordination gauges after a multi-agent execution.

        Called by ``CoordinationCollector`` post-execution.

        Args:
            efficiency: Coordination efficiency ratio (0.0-1.0).
            overhead_percent: Coordination overhead percentage.

        Raises:
            ValueError: If either input is NaN, Inf, or negative, or
                if ``efficiency`` exceeds 1.0 (the documented upper
                bound for the ratio).
        """
        require_non_negative("record_coordination_metrics: efficiency", efficiency)
        if efficiency > 1.0:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="coordination_metrics",
                reason="efficiency_out_of_range",
                efficiency=efficiency,
            )
            msg = (
                f"record_coordination_metrics: efficiency must be <= 1.0;"
                f" got {efficiency!r}"
            )
            raise ValueError(msg)
        require_non_negative(
            "record_coordination_metrics: overhead_percent",
            overhead_percent,
        )
        self._coordination_efficiency.set(efficiency)
        self._coordination_overhead_percent.set(overhead_percent)
        logger.debug(
            METRICS_COORDINATION_RECORDED,
            efficiency=efficiency,
            overhead_percent=overhead_percent,
        )

    def record_escalation_queue_depth(
        self,
        *,
        department: str,
        depth: int,
    ) -> None:
        """Set the escalation queue depth gauge for a department.

        ``department`` is validated against the live registry snapshot
        seeded by :meth:`refresh`; unknown values raise ``ValueError``
        and are dropped via the metrics-hub safe-record decorator.

        Args:
            department: Department name owning the escalation queue.
            depth: Current count of pending escalations.
        """
        if not department:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="escalation_queue_depth",
                reason="empty_department",
            )
            msg = "record_escalation_queue_depth: department must be non-empty"
            raise ValueError(msg)
        validate_department(department)
        require_non_negative("record_escalation_queue_depth: depth", depth)
        self._escalation_queue_depth.labels(department=department).set(depth)

    def record_agent_identity_change(
        self,
        *,
        agent_id: str,
        change_type: str,
    ) -> None:
        """Increment the agent identity change counter.

        ``agent_id`` is validated against the live agent-registry
        snapshot seeded by :meth:`refresh`; unknown ids raise
        ``ValueError`` and are dropped by the metrics-hub safe-record
        decorator. ``change_type`` is bounded by
        :data:`VALID_IDENTITY_CHANGE_TYPES`.
        """
        if not agent_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_identity_change",
                reason="empty_agent_id",
            )
            msg = "record_agent_identity_change: agent_id must be non-empty"
            raise ValueError(msg)
        validate_agent_id(agent_id)
        require_label(
            "record_agent_identity_change: change_type",
            change_type,
            VALID_IDENTITY_CHANGE_TYPES,
        )
        self._agent_identity_changes.labels(
            agent_id=agent_id,
            change_type=change_type,
        ).inc()

    def record_workflow_execution(
        self,
        *,
        workflow_definition_id: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Observe a completed workflow execution in the duration histogram.

        ``workflow_definition_id`` must be the stable workflow
        definition id (bounded), NOT a per-run execution id. The
        snapshot validator additionally rejects ids that aren't in
        the active workflow-definition repository so an orphan
        execution can't bloat label cardinality.
        """
        if not workflow_definition_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="workflow_execution",
                reason="empty_workflow_definition_id",
            )
            msg = "record_workflow_execution: workflow_definition_id must be non-empty"
            raise ValueError(msg)
        validate_workflow_definition_id(workflow_definition_id)
        require_label(
            "record_workflow_execution: status",
            status,
            VALID_WORKFLOW_EXECUTION_STATUSES,
        )
        require_non_negative(
            "record_workflow_execution: duration_seconds",
            duration_seconds,
        )
        self._workflow_execution_duration.labels(
            workflow_definition_id=workflow_definition_id,
            status=status,
        ).observe(duration_seconds)

    def record_client_disconnect(
        self,
        *,
        transport: str,
        reason: str,
    ) -> None:
        """Increment the client-disconnect counter.

        Wired into SSE / WebSocket / MCP-stdio disconnect handlers so
        ops can alert on
        ``rate(synthorg_client_disconnects_total{reason="transport_error"}[5m])``.
        Both labels are bounded vocabularies so the time-series
        cardinality is fixed at 12 (transports x reasons).
        """
        require_label(
            "client disconnect transport",
            transport,
            VALID_DISCONNECT_TRANSPORTS,
        )
        require_label(
            "client disconnect reason",
            reason,
            VALID_DISCONNECT_REASONS,
        )
        self._client_disconnects.labels(
            transport=transport,
            reason=reason,
        ).inc()
        logger.info(
            CLIENT_DISCONNECTED,
            transport=transport,
            reason=reason,
        )

    def record_approval_decision(self, *, outcome: str) -> None:
        """Increment the approval-decision counter.

        Args:
            outcome: One of ``"approved"`` / ``"rejected"`` /
                ``"expired"``.

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_APPROVAL_OUTCOMES`.
        """
        require_label("approval outcome", outcome, VALID_APPROVAL_OUTCOMES)
        self._approval_decisions.labels(outcome=outcome).inc()

    def record_escalation_outcome(self, *, outcome: str) -> None:
        """Increment the escalation-outcome counter.

        Args:
            outcome: One of ``"resolved"`` / ``"escalated_to_human"``
                / ``"auto_resolved"`` / ``"notify_failed"`` /
                ``"sweeper_failed"``.

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_ESCALATION_OUTCOMES`.
        """
        require_label("escalation outcome", outcome, VALID_ESCALATION_OUTCOMES)
        self._escalation_outcomes.labels(outcome=outcome).inc()

    def record_blueprint_instantiation(
        self,
        *,
        outcome: str,
        blueprint_name: str | None = None,
        duration_sec: float | None = None,
    ) -> None:
        """Increment the blueprint-instantiation counter.

        Args:
            outcome: One of :data:`VALID_BLUEPRINT_OUTCOMES`.
            blueprint_name: Blueprint slug (logged, not labelled --
                cardinality is held by the bounded outcome label).
            duration_sec: Wall-clock duration of the instantiation
                (logged, not observed -- this counter is success-rate
                focused; use ``synthorg_workflow_execution_seconds``
                for runtime quantiles).

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_BLUEPRINT_OUTCOMES` or *duration_sec* is
                negative.
        """
        require_label("blueprint outcome", outcome, VALID_BLUEPRINT_OUTCOMES)
        if duration_sec is not None:
            require_non_negative(
                "record_blueprint_instantiation: duration_sec",
                duration_sec,
            )
        self._blueprint_instantiations.labels(outcome=outcome).inc()
        logger.info(
            BLUEPRINT_INSTANTIATE_OUTCOME,
            blueprint_name=blueprint_name,
            outcome=outcome,
            duration_sec=duration_sec,
        )

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
            tool=tool,
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

    def record_audit_chain_verification(
        self,
        *,
        outcome: str,
        entries_checked: int,
        first_break_position: int | None = None,
    ) -> None:
        """Record an audit chain integrity verification.

        Increments once per ``verify_chain()`` call regardless of
        chain depth, so dashboards can compute a true per-call
        ``broken`` rate via
        ``rate(synthorg_audit_chain_verifications_total{outcome="broken"}[1h])``.

        Args:
            outcome: ``"valid"`` or ``"broken"``.
            entries_checked: Number of chain entries verified.
            first_break_position: Position of the first broken link
                (logged for offline incident triage; not labelled).

        Raises:
            ValueError: If *outcome* is invalid or *entries_checked*
                is negative.
        """
        require_label(
            "audit verification outcome",
            outcome,
            VALID_AUDIT_VERIFICATION_OUTCOMES,
        )
        require_non_negative(
            "record_audit_chain_verification: entries_checked",
            entries_checked,
        )
        if first_break_position is not None:
            require_non_negative(
                "record_audit_chain_verification: first_break_position",
                first_break_position,
            )
        self._audit_chain_verifications.labels(outcome=outcome).inc()
        logger.info(
            SECURITY_AUDIT_CHAIN_VERIFY_OUTCOME,
            outcome=outcome,
            entries_checked=entries_checked,
            first_break_position=first_break_position,
        )

    def record_ws_connection_lifetime(
        self,
        *,
        transport: str,
        duration_sec: float,
    ) -> None:
        """Observe a WebSocket connection's wall-clock lifetime.

        Called from the WS controller's close path; ``duration_sec``
        is the time between the auth-ok handshake and the close
        notification.
        """
        require_label("ws transport", transport, VALID_WS_TRANSPORTS)
        require_finite("record_ws_connection_lifetime: duration_sec", duration_sec)
        self._ws_connection_lifetime.labels(transport=transport).observe(
            duration_sec,
        )

    def record_ws_revalidation_outcome(self, *, outcome: str) -> None:
        """Increment the per-frame revalidation outcome counter."""
        require_label(
            "ws revalidation outcome",
            outcome,
            VALID_WS_REVALIDATION_OUTCOMES,
        )
        self._ws_revalidation_outcomes.labels(outcome=outcome).inc()

    def set_ws_active_connections(self, *, count: int) -> None:
        """Replace the gauge with the current connection count."""
        require_non_negative("set_ws_active_connections: count", count)
        self._ws_active_connections.set(count)

    def record_pg_pool_size(self, *, backend: str, size: int) -> None:
        """Replace the configured pool-size gauge."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_non_negative("record_pg_pool_size: size", size)
        self._pg_pool_size.labels(backend=backend).set(size)

    def record_pg_pool_active(self, *, backend: str, active: int) -> None:
        """Replace the active-connection gauge."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_non_negative("record_pg_pool_active: active", active)
        self._pg_pool_active_connections.labels(backend=backend).set(active)

    def record_pg_pool_acquire(
        self,
        *,
        backend: str,
        duration_sec: float,
    ) -> None:
        """Observe a connection-acquisition latency."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_finite("record_pg_pool_acquire: duration_sec", duration_sec)
        self._pg_pool_acquire_duration.labels(backend=backend).observe(
            duration_sec,
        )

    def record_pg_pool_exhausted(self, *, backend: str) -> None:
        """Increment the pool-exhaustion counter."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        self._pg_pool_exhausted.labels(backend=backend).inc()
