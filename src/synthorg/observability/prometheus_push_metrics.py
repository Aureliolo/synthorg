"""Prometheus metric families that are push-updated by call sites.

Extracted from :mod:`synthorg.observability.prometheus_collector` to
keep that module under the 800-line ceiling mandated by CLAUDE.md.
The ``PushMetrics`` container instantiates every Counter / Histogram
/ Gauge that middleware, cost-recording, the audit sink, and the
OTLP handler push data into, and exposes them as attributes so the
collector can forward ``record_*`` calls with a single dot-access.

No business logic lives here -- the collector still owns the
validation, cardinality guards, and public API.
"""

from prometheus_client import CollectorRegistry, Gauge, Histogram
from prometheus_client import Counter as PromCounter


class PushMetrics:
    """Container for push-updated Prometheus metric families."""

    def __init__(
        self,
        *,
        registry: CollectorRegistry,
        prefix: str,
    ) -> None:
        # -- Provider token / cost counters --------------------------
        self.provider_tokens = PromCounter(
            f"{prefix}_provider_tokens_total",
            "Tokens consumed per provider, model, and direction",
            ["provider", "model", "direction"],
            registry=registry,
        )
        self.provider_cost = PromCounter(
            f"{prefix}_provider_cost_total",
            "Accumulated cost in the configured currency per provider and model",
            ["provider", "model"],
            registry=registry,
        )

        # -- API request histogram -----------------------------------
        self.api_request_duration = Histogram(
            f"{prefix}_api_request_duration_seconds",
            "HTTP request handler duration",
            ["method", "route", "status_class"],
            buckets=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ),
            registry=registry,
        )

        # -- Task counters + histogram -------------------------------
        self.task_runs = PromCounter(
            f"{prefix}_task_runs_total",
            "Task completions by outcome",
            ["outcome"],
            registry=registry,
        )
        self.task_duration = Histogram(
            f"{prefix}_task_duration_seconds",
            "Task execution duration by outcome",
            ["outcome"],
            buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0),
            registry=registry,
        )

        # -- Tool counters + histogram -------------------------------
        self.tool_invocations = PromCounter(
            f"{prefix}_tool_invocations_total",
            "Tool invocation count by tool and outcome",
            ["tool_name", "outcome"],
            registry=registry,
        )
        self.tool_duration = Histogram(
            f"{prefix}_tool_duration_seconds",
            "Tool invocation duration by tool and outcome",
            ["tool_name", "outcome"],
            buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 30.0, 120.0),
            registry=registry,
        )

        # -- Audit chain metrics -------------------------------------
        self.audit_chain_appends = PromCounter(
            f"{prefix}_audit_chain_appends_total",
            "Audit chain append operations by status",
            ["status"],
            registry=registry,
        )
        self.audit_chain_depth = Gauge(
            f"{prefix}_audit_chain_depth",
            "Current audit hash chain length",
            registry=registry,
        )
        self.audit_chain_last_append_ts = Gauge(
            f"{prefix}_audit_chain_last_append_timestamp_seconds",
            "Unix timestamp of the last audit chain append",
            registry=registry,
        )

        # -- OTLP export health --------------------------------------
        self.otlp_export_batches = PromCounter(
            f"{prefix}_otlp_export_batches_total",
            "OTLP export batches by kind and outcome",
            ["kind", "outcome"],
            registry=registry,
        )
        self.otlp_export_dropped = PromCounter(
            f"{prefix}_otlp_export_dropped_records_total",
            "OTLP records dropped (queue full, export failed past retries)",
            ["kind"],
            registry=registry,
        )

        # -- Escalation queue depth (per department) -----------------
        self.escalation_queue_depth = Gauge(
            f"{prefix}_escalation_queue_depth",
            "Pending escalations awaiting decision",
            ["department"],
            registry=registry,
        )

        # -- Agent identity change counter ---------------------------
        self.agent_identity_changes = PromCounter(
            f"{prefix}_agent_identity_version_changes_total",
            "Agent identity version changes",
            ["agent_id", "change_type"],
            registry=registry,
        )

        # -- Workflow execution duration histogram -------------------
        # The ``workflow_definition_id`` label is the stable workflow
        # definition identifier (NOT a per-execution id) to keep
        # cardinality bounded by the number of workflows an operator
        # has defined.
        #
        # Workflows can be anywhere from a few seconds (quick
        # classification routines) to hours (multi-phase roadmaps).
        # Prometheus's default buckets top out around 10s, which
        # would collapse p95/p99 for anything long-running. The
        # explicit buckets below span sub-second to 1h so quantiles
        # stay meaningful across both regimes.
        self.workflow_execution_duration = Histogram(
            f"{prefix}_workflow_execution_seconds",
            "Workflow execution duration",
            ["workflow_definition_id", "status"],
            buckets=(0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0, 1800.0, 3600.0),
            registry=registry,
        )

        # -- Provider error counter ----------------------------------
        # ``error_class`` is bounded via
        # :data:`VALID_PROVIDER_ERROR_CLASSES`; ``model`` is emitted
        # as-is since the set of models is configured (not unbounded
        # user input).
        self.provider_errors = PromCounter(
            f"{prefix}_provider_errors_total",
            "Provider call errors by provider, model, and error class",
            ["provider", "model", "error_class"],
            registry=registry,
        )

        # -- Cache operation counter (hit / miss / evict) -------------
        # Labels are bounded via :data:`VALID_CACHE_NAMES` and
        # :data:`VALID_CACHE_OUTCOMES` so adding a new cache is an
        # explicit allowlist edit, not a silent cardinality bloom.
        self.cache_operations = PromCounter(
            f"{prefix}_cache_operations_total",
            "In-process cache operations by cache and outcome",
            ["cache_name", "outcome"],
            registry=registry,
        )

        # -- API error classification counter -------------------------
        # ``category`` tracks the RFC 9457 error taxonomy for 4xx/5xx
        # responses; ``status_class`` reuses
        # :data:`VALID_STATUS_CLASSES`.  The existing
        # ``synthorg_api_request_duration_seconds_count`` series
        # covers request-rate; this one partitions failures by the
        # taxonomy operators filter on.
        self.api_error_classification = PromCounter(
            f"{prefix}_api_error_classification_total",
            "API error responses by category and HTTP status class",
            ["category", "status_class"],
            registry=registry,
        )

        # -- Client disconnect counter --------------------------------
        # Single counter with two bounded labels (max 12 series) so
        # one alert rule covers SSE / WebSocket / MCP-stdio. Labels
        # are validated via :data:`VALID_DISCONNECT_TRANSPORTS` and
        # :data:`VALID_DISCONNECT_REASONS`.
        self.client_disconnects = PromCounter(
            f"{prefix}_client_disconnects_total",
            "Client transport disconnections by transport and reason",
            ["transport", "reason"],
            registry=registry,
        )
