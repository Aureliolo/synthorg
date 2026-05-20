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

        # -- Security audit log fill ratio ---------------------------
        # Bounded gauge in [0.0, 1.0] tracking ``len(_entries) /
        # _max_entries`` on the in-memory ``AuditLog``. A value near
        # 1.0 means eviction is imminent (the deque drops the oldest
        # entry on the next ``record``), which operators want to alert
        # on so an investigation can preserve evidence before it is
        # lost.
        self.security_audit_log_fill_ratio = Gauge(
            f"{prefix}_security_audit_log_fill_ratio",
            "Security audit log occupancy as a fraction of max_entries",
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
        # Single counter with two bounded labels (cardinality is
        # ``len(VALID_DISCONNECT_TRANSPORTS) * len(VALID_DISCONNECT_REASONS)``
        # ; currently 4 transports x 4 reasons = 16 series) so one
        # alert rule covers SSE / WebSocket / MCP-stdio / MCP-HTTP.
        # Labels are validated via :data:`VALID_DISCONNECT_TRANSPORTS`
        # and :data:`VALID_DISCONNECT_REASONS`.
        self.client_disconnects = PromCounter(
            f"{prefix}_client_disconnects_total",
            "Client transport disconnections by transport and reason",
            ["transport", "reason"],
            registry=registry,
        )

        # -- Approval decisions counter ------------------------------
        # Outcome label is bounded via ``VALID_APPROVAL_OUTCOMES``.
        # Cardinality fixed at 4 series.
        self.approval_decisions = PromCounter(
            f"{prefix}_approval_decisions_total",
            "Approval-gate terminal decisions by outcome",
            ["outcome"],
            registry=registry,
        )

        # -- Escalation outcomes counter -----------------------------
        # Outcome label is bounded via ``VALID_ESCALATION_OUTCOMES``.
        # Disjoint from ``approval_decisions`` because the two flows
        # have different terminal vocabularies and live in different
        # modules; combining them under one Counter with a ``kind``
        # label would force a synthetic taxonomy on dashboards.
        self.escalation_outcomes = PromCounter(
            f"{prefix}_escalation_outcomes_total",
            "Conflict-resolution escalation terminal outcomes",
            ["outcome"],
            registry=registry,
        )

        # Per-project workspace push-queue events. ``outcome`` is
        # low-cardinality (``enqueued`` / ``merged``); ``project_id`` is
        # deliberately NOT a label (unbounded cardinality) -- it stays
        # in the structured logs only.
        self.push_queue_events = PromCounter(
            f"{prefix}_push_queue_events_total",
            "Workspace merge+push queue events",
            ["outcome"],
            registry=registry,
        )

        # -- Workflow blueprint instantiation counter ----------------
        # Single neutral terminal counter so dashboards can compute
        # success rate as
        # ``rate(.._total{outcome="success"}) / rate(.._total)``
        # without double-counting. Outcome is bounded via
        # ``VALID_BLUEPRINT_OUTCOMES``.
        self.blueprint_instantiations = PromCounter(
            f"{prefix}_blueprint_instantiations_total",
            "Workflow blueprint instantiation attempts by outcome",
            ["outcome"],
            registry=registry,
        )

        # -- Settings mutations counter ------------------------------
        # Namespace label is bounded via ``VALID_SETTINGS_NAMESPACES``
        # (mirrors filenames in ``settings/definitions/``). Action
        # (set / set_many / delete / delete_namespace) is
        # intentionally NOT a label so the dashboard slices by
        # namespace only -- the operator-facing question is "which
        # namespace is being mutated", and adding ``action`` would
        # quadruple cardinality (22 namespaces x 4 actions = 88
        # series) without a dashboard that asks for that breakdown.
        self.settings_mutations = PromCounter(
            f"{prefix}_settings_mutations_total",
            "Settings mutations by namespace",
            ["namespace"],
            registry=registry,
        )

        # -- MCP handler outcome counter + latency histogram ---------
        # Tighter low-end resolution than the existing
        # ``tool_duration`` histogram (which extends to 120s for
        # provider-bound tools). MCP handlers are service-boundary
        # calls, mostly sub-second; the bucket span below covers
        # 1ms to 10s with seven sub-100ms buckets.
        self.mcp_handler_outcomes = PromCounter(
            f"{prefix}_mcp_handler_outcomes_total",
            "MCP handler invocations by tool and outcome",
            ["tool", "outcome"],
            registry=registry,
        )
        self.mcp_handler_duration = Histogram(
            f"{prefix}_mcp_handler_duration_seconds",
            "MCP handler invocation duration by tool and outcome",
            ["tool", "outcome"],
            buckets=(
                0.001,
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

        # -- Budget query latency histogram --------------------------
        # Pure read-path, SQLite-bound; p95 should be sub-25ms. The
        # bucket span below caps at 1s to keep the dashboard's p95/p99
        # series meaningful (a budget query that takes >1s is itself
        # a regression worth alerting on).
        self.budget_query_duration = Histogram(
            f"{prefix}_budget_query_duration_seconds",
            "Budget read-path query duration by query type",
            ["query_type"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
            registry=registry,
        )

        # -- Audit chain integrity verification counter --------------
        # Outcome is bounded via ``VALID_AUDIT_VERIFICATION_OUTCOMES``
        # (``valid`` / ``broken``). Increments once per
        # ``verify_chain()`` call -- a panel querying
        # ``increase(.._total{outcome="broken"}[1h])`` is the alert
        # signal for chain tampering / corruption.
        self.audit_chain_verifications = PromCounter(
            f"{prefix}_audit_chain_verifications_total",
            "Audit chain integrity verifications by outcome",
            ["outcome"],
            registry=registry,
        )

        # -- WS lifetime / revalidation / concurrent connections -----
        # Wall-time histogram of WS connection lifetimes; alerts on
        # truncated tail (clients dropping shortly after auth) and on
        # silent long-lived hangs.
        self.ws_connection_lifetime = Histogram(
            f"{prefix}_ws_connection_lifetime_seconds",
            "WebSocket connection lifetime in seconds, by transport",
            ["transport"],
            buckets=(1.0, 5.0, 30.0, 60.0, 300.0, 1800.0, 3600.0, 14400.0),
            registry=registry,
        )
        self.ws_revalidation_outcomes = PromCounter(
            f"{prefix}_ws_revalidation_total",
            ("Per-frame WS revalidation outcomes (pass / fail / budget_exhausted)"),
            ["outcome"],
            registry=registry,
        )
        self.ws_active_connections = Gauge(
            f"{prefix}_ws_active_connections",
            "Currently-open WebSocket connections",
            registry=registry,
        )

        # -- Postgres connection pool metrics ------------------------
        # ``backend`` label allows multiple Postgres pools to coexist
        # (primary read-write + read-only replicas). The pool's
        # ``stats()`` snapshot drives the gauges; the counter ticks on
        # every saturation event.
        self.pg_pool_size = Gauge(
            f"{prefix}_pg_pool_size",
            "Configured Postgres connection pool size",
            ["backend"],
            registry=registry,
        )
        self.pg_pool_active_connections = Gauge(
            f"{prefix}_pg_pool_active_connections",
            "Active connections currently checked out of the pool",
            ["backend"],
            registry=registry,
        )
        self.pg_pool_acquire_duration = Histogram(
            f"{prefix}_pg_pool_acquire_duration_seconds",
            "Wall time spent waiting for a Postgres connection",
            ["backend"],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
            registry=registry,
        )
        self.pg_pool_exhausted = PromCounter(
            f"{prefix}_pg_pool_exhausted_total",
            "Pool acquisition timed out (no connection available)",
            ["backend"],
            registry=registry,
        )
