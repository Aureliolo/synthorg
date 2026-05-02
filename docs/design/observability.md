---
title: Observability & Performance Tracking
description: Performance tracking configuration, structured logging with 11 default sinks, sensitive-field redaction, correlation IDs, per-domain routing, event taxonomy, third-party logger taming, and runtime-editable settings.
---

# Observability & Performance Tracking

Observability in SynthOrg spans three concerns: performance tracking (quality scoring weights, LLM judge, trend detection), structured logging (11 default sinks with per-domain routing, correlation IDs, sensitive-field redaction), and metrics export (Prometheus `/metrics` + OTLP). All three are configured through the same settings subsystem and refresh without restart where safe.

The root logger ships at `INFO` so HTTP log sinks and sampled streams stay cheap. Agent-trace loggers (`synthorg.engine`, `synthorg.memory`) default to `INFO` as well. Set `observability.root_level=debug` (or `logging.root_level: DEBUG` in the company YAML) for system-wide DEBUG, or set `observability.per_logger_levels` (or `config.logger_levels` in YAML) to raise specific loggers to DEBUG without the full firehose.

---

## Performance Tracking Configuration

The `performance` namespace in the company YAML configures the performance tracking
subsystem, including quality scoring weights, LLM judge settings, and trend detection
thresholds. These values flow through `RootConfig.performance` into
`_build_performance_tracker` at app startup.

```yaml
performance:
  min_data_points: 5              # Minimum data points for meaningful aggregation
  windows:
    - "7d"
    - "30d"
    - "90d"
  improving_threshold: 0.05       # Slope threshold for improving trend
  declining_threshold: -0.05      # Slope threshold for declining trend
  quality_judge_model: null       # Model ID for LLM quality judge (null = disabled)
  quality_judge_provider: null    # Provider name (null = auto from first available)
  quality_ci_weight: 0.4          # Weight for CI signal in composite score
  quality_llm_weight: 0.6         # Weight for LLM judge in composite score
  llm_sampling_rate: 0.01         # Fraction of events sampled by LLM calibration
  llm_sampling_model: null        # Model for calibration sampling (null = disabled)
  collaboration_weights: null      # Custom weights for collaboration scoring (null = defaults)
  calibration_retention_days: 90  # Days to retain calibration records
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `quality_judge_model` | `string` or `null` | `null` | Model ID for quality LLM judge. `null` disables the judge. |
| `quality_judge_provider` | `string` or `null` | `null` | Provider name for the judge. Requires `quality_judge_model`. |
| `quality_ci_weight` | `float` | `0.4` | Weight for CI signal (0.0--1.0). Must sum to 1.0 with `quality_llm_weight`. |
| `quality_llm_weight` | `float` | `0.6` | Weight for LLM judge (0.0--1.0). Must sum to 1.0 with `quality_ci_weight`. |
| `min_data_points` | `int` | `5` | Minimum data points for meaningful metric aggregation. |
| `windows` | `list[string]` | `["7d", "30d", "90d"]` | Time window labels for rolling metrics (at least one required). |
| `improving_threshold` | `float` | `0.05` | Slope above which a metric trend is classified as "improving". |
| `declining_threshold` | `float` | `-0.05` | Slope below which a metric trend is classified as "declining". |
| `collaboration_weights` | `object` or `null` | `null` | Custom weights for collaboration scoring components. `null` uses defaults. |
| `llm_sampling_rate` | `float` | `0.01` | Fraction of task events sampled for LLM calibration. |
| `llm_sampling_model` | `string` or `null` | `null` | Model ID for LLM calibration sampling. `null` disables sampling. |
| `calibration_retention_days` | `int` | `90` | Days to retain calibration records before expiry. |

!!! note "Validation Rules"
    - `quality_ci_weight + quality_llm_weight` must equal `1.0` (tolerance: 1e-6)
    - `improving_threshold` must be strictly greater than `declining_threshold`
    - `quality_judge_provider` requires `quality_judge_model` to be set

## Structured Logging

Structured logging pipeline built on **structlog** + stdlib, with automatic sensitive field
redaction, async-safe correlation tracking, and per-domain log routing.

### Sink Layout

Eleven default sinks, activated at startup via `bootstrap_logging()`:

| Sink | Type | Level | Format | Routes | Description |
|------|------|-------|--------|--------|-------------|
| Console | stderr | INFO | Colored text | All loggers | Human-readable development output |
| `synthorg.log` | File | INFO | JSON | All loggers EXCEPT `api.request.started` / `api.request.completed` | Main application log (catch-all). Request-lifecycle events from `synthorg.api.middleware` are excluded by an event-name filter so the main log is not buried under per-request noise -- those events live in `access.log` only. |
| `audit.log` | File | INFO | JSON | `synthorg.security.*`, `synthorg.hr.*`, `synthorg.observability.*` | Audit-relevant events (security, HR, observability) |
| `errors.log` | File | ERROR | JSON | All loggers | Errors and above only |
| `agent_activity.log` | File | DEBUG | JSON | `synthorg.engine.*`, `synthorg.core.*`, `synthorg.communication.*`, `synthorg.tools.*`, `synthorg.memory.*` | Agent execution, communication, tools, and memory |
| `cost_usage.log` | File | INFO | JSON | `synthorg.budget.*`, `synthorg.providers.*` | Cost records and provider calls |
| `debug.log` | File | DEBUG only (exact match) | JSON | All loggers | Full DEBUG trace. Pinned to `level == DEBUG` exactly so the file stays empty when nothing emits DEBUG instead of accidentally collecting INFO+ as a duplicate of `synthorg.log`. |
| `access.log` | File | INFO | JSON | `synthorg.api.*` | HTTP request/response access log |
| `persistence.log` | File | INFO | JSON | `synthorg.persistence.*` | Database operations, migrations, CRUD |
| `configuration.log` | File | INFO | JSON | `synthorg.settings.*`, `synthorg.config.*` | Settings resolution, config loading |
| `backup.log` | File | INFO | JSON | `synthorg.backup.*` | Backup/restore lifecycle |

In addition to the 11 default sinks, three shipping sink types are available for centralized
log aggregation and telemetry export:

| Sink Type | Transport | Format | Description |
|-----------|-----------|--------|-------------|
| Syslog | UDP or TCP to a configurable endpoint | JSON | Ship structured logs to rsyslog, syslog-ng, or Graylog |
| HTTP | Batched POST to a configurable URL | JSON array | Ship log batches to any JSON-accepting endpoint |
| OTLP | HTTP POST to an OpenTelemetry collector | OTLP JSON | Map structlog events to OTLP log records with correlation IDs as trace context |

### OTLP transport: HTTP only

SynthOrg ships only the **HTTP** OTLP exporter (`opentelemetry.exporter.otlp.proto.http`). The gRPC transport is not supported. The rationale is operational:

- HTTP is a single lightweight dependency on `protobuf` + `requests`; the gRPC transport pulls in `grpcio` (roughly a 30 MB wheel) that most operators do not already have installed.
- OTLP/HTTP and OTLP/gRPC share the same payload schema, so switching later is a dependency change rather than a protocol redesign.
- Every OpenTelemetry collector supports both; operators who prefer gRPC can run a side-car collector and point SynthOrg at its HTTP receiver.

If a concrete deployment needs gRPC directly, file an enhancement issue with the target environment; there is no open design blocker, only a missing dependency opt-in.

The HTTP sink sends raw JSON arrays.  Backends that expect different payload formats
(e.g., Grafana Loki's `/loki/api/v1/push`, Elasticsearch's `/_bulk`) require a
collector/proxy (Promtail, Logstash, Vector, etc.) to translate the payload.

Shipping sinks are catch-all (no logger name routing) and are configured at runtime via the
`custom_sinks` setting or YAML. See the [Centralized Logging](../guides/centralized-logging.md)
guide for configuration examples and deployment patterns.

Logger name routing is implemented via `_LoggerNameFilter` on file handlers. Sinks without
explicit routing are catch-all (accept all loggers at their configured level).

Exception formatting differs between sink types: `format_exc_info` is applied only to sinks
with `json_format=True` (converting `exc_info` tuples to formatted traceback strings for
serialization). Sinks with `json_format=False` (the default console sink) omit this
processor because `ConsoleRenderer` handles exception rendering natively.

### Log Directory

- **Docker**: `/data/logs/` (under the `synthorg-data` volume, persisted across restarts)
- **Local dev**: `logs/` relative to working directory (default)
- **Override**: `SYNTHORG_LOG_DIR` env var

### Rotation and Compression

File sinks use `RotatingFileHandler` by default (10 MB max, 5 backup files). Alternative:
`WatchedFileHandler` for external logrotate (`rotation.strategy: external` in config).

Rotated backup files can be automatically gzip-compressed by setting `compress_rotated: true`
in the rotation config. Compressed backups are stored as `.log.N.gz` instead of `.log.N`,
typically achieving 5--10x size reduction for structured JSON logs. Compression is off by
default for backward compatibility. `compress_rotated` is only supported with the built-in
rotation strategy; it is rejected when `rotation.strategy` is set to `external`.

### Sensitive Field Redaction

The `sanitize_sensitive_fields` processor automatically redacts values for keys matching:
`password`, `secret`, `token`, `api_key`, `api_secret`, `authorization`, `credential`,
`private_key`, `bearer`, `session`. Redaction applies at all nesting depths in structured
log events. Redacted values are replaced with `"**REDACTED**"`.

### Correlation Tracking

Three correlation IDs propagated via `contextvars` (async-safe):

- **`request_id`**: Bound per HTTP request by `RequestLoggingMiddleware`. Links all log
  events during a single API call.
- **`task_id`**: Bound per task execution. Links agent activity to a specific task.
- **`agent_id`**: Bound per agent execution context.

All three are automatically injected into every log event by `merge_contextvars` in the
structlog processor chain.

### Per-Logger Levels

Default levels per domain module (overridable via `LogConfig.logger_levels`):

| Logger | Default Level |
|--------|---------------|
| `synthorg.engine` | INFO |
| `synthorg.memory` | INFO |
| `synthorg.core` | INFO |
| `synthorg.communication` | INFO |
| `synthorg.providers` | INFO |
| `synthorg.budget` | INFO |
| `synthorg.security` | INFO |
| `synthorg.tools` | INFO |
| `synthorg.api` | INFO |
| `synthorg.cli` | INFO |
| `synthorg.config` | INFO |
| `synthorg.templates` | INFO |

### Event Taxonomy

100+ domain-specific event constant modules under `observability/events/` (one per subsystem:
api, budget, risk_budget, reporting, blueprint, workflow_version, tool, git, engine, communication, security, etc.). Every log call uses a typed constant
(e.g., `API_REQUEST_STARTED`, `BUDGET_RECORD_ADDED`) for consistent, grep-friendly event
names. Format: `"<domain>.<noun>.<verb>"` (e.g., `"api.request.started"`).

**MCP handler events (`observability/events/mcp.py`):**

| Constant | Level | When fired |
|----------|-------|------------|
| `MCP_SERVER_INVOKE_START` | DEBUG | Invoker dispatches a tool call. |
| `MCP_SERVER_INVOKE_SUCCESS` | DEBUG | Handler returned without exception. |
| `MCP_SERVER_INVOKE_FAILED` | WARNING | Tool/handler not found, or handler raised an uncaught exception. |
| `MCP_HANDLER_INVOKE_SUCCESS` | INFO | Handler completed its service shim successfully (state transition; every tool invocation that mutates or produces a result is auditable). |
| `MCP_HANDLER_INVOKE_FAILED` | WARNING | Handler caught a service-layer or domain error and returned an `err(...)` envelope. |
| `MCP_HANDLER_ARGUMENT_INVALID` | WARNING | Caller input failed `require_arg` / pagination / enum coercion; returned `domain_code="invalid_argument"`. |
| `MCP_HANDLER_GUARDRAIL_VIOLATED` | WARNING | Destructive-op guardrail rejected the call (missing `confirm`/`reason`/`actor`); returned `domain_code="guardrail_violated"`. |
| `MCP_DESTRUCTIVE_OP_EXECUTED` | INFO | Audit trail for a successful destructive operation; carries `actor_agent_id`, `reason`, and the target id. |
| `MCP_HANDLER_NOT_IMPLEMENTED` | WARNING | Handler returned `not_supported`. Two emission paths: (a) the MCP tool is registered but no concrete handler is wired (placeholder tools via `make_placeholder_handler`); (b) a live handler caught a typed `BackendUnsupportedError` from the persistence layer and forwarded it through `not_supported()`. The fine-tune lifecycle tools running against a backend that lacks fine-tune repos are the concrete example. The wire envelope is identical in both cases; operators disambiguate via the **event name** itself (`MCP_HANDLER_NOT_IMPLEMENTED` for both, vs `MCP_HANDLER_CAPABILITY_GAP` for the primitive-gap path vs `MCP_HANDLER_SERVICE_FALLBACK` for the legacy fallback). The tool / handler name in the payload then narrows which of the two `NOT_IMPLEMENTED` sub-cases fired. |
| `MCP_HANDLER_SERVICE_FALLBACK` | WARNING | Legacy helper `service_fallback()` emitted; META-MCP-2 removed every call site and the integration sweep at `tests/integration/mcp/test_tool_surface.py` asserts zero emissions. Helper retained for future surgical use. |
| `MCP_HANDLER_CAPABILITY_GAP` | INFO | Live handler whose underlying primitive does not expose the required method; wire envelope matches `not_supported` (`domain_code="not_supported"`) but the event channel distinguishes "primitive gap" from "handler unwired" and from "backend-unsupported". Some primitives may never grow the method (infrastructure limits of the selected backend); others may acquire it in a later release. The event records the current gap, not a forward commitment. |
| `MCP_HANDLER_LAZY_SERVICE_INIT` | DEBUG | Handler constructed its service facade per-call because `app_state` had not wired one. Telemetry for legacy bootstrap paths. |
| `MCP_HANDLERS_BUILT` | DEBUG (ERROR on duplicate key) | Handler registry successfully composed from the 15 domain modules. |

All MCP handler log calls go through `logger.warning(EVENT, error_type=type(exc).__name__, error=safe_error_description(exc))` on credential-sensitive paths (never `logger.exception(..., error=str(exc))`) to avoid leaking secrets through traceback frame-locals (SEC-1).

**API entry-point boundary events (`observability/events/api.py`):**

| Constant | Level | When fired |
|----------|-------|------------|
| `API_BOUNDARY_VALIDATION_FAILED` | WARNING | `synthorg.api.boundary.parse_typed` rejected a payload at one of the registered API entry-point boundaries (MCP handler args, JWT decode, WebSocket control message, audit-chain payload, A2A JSON-RPC params, settings security export). Carries `boundary` (hardcoded literal label for log search), `error_type`, `error_count`, `error` (redacted via `safe_error_description`), `error_locations` (first 5 field paths), and `truncated` (true when `error_count > 5` so operators know the listed locations are a sample). The helper re-raises the `ValidationError` for the caller to translate into the appropriate HTTP / RPC / envelope response. |

**Self-improvement meta-loop events (`observability/events/meta.py`):**

| Constant | Level | When fired |
|----------|-------|------------|
| `META_CYCLE_TRIGGERED` | INFO | `SelfImprovementService.trigger_cycle` completed successfully. Carries `cycle_id`, `proposals_count`, and `duration_seconds`. Emitted by both the in-process meta loop and the `synthorg_meta_trigger_cycle` MCP tool. |
| `META_CYCLE_TRIGGER_FAILED` | WARNING | `trigger_cycle` could not produce an `ImprovementCycleResult`. `reason` is `"no_snapshot_builder"` (precondition), `"snapshot_builder_failed"` (builder raised), or `"run_cycle_failed"` (cycle body raised). Logs `error_type` and a `safe_error_description(exc)` on the latter two paths so callers see the failure even when they do not catch the re-raise. |

**Workflow version events (`observability/events/workflow_version.py`):**

| Constant | Level | When fired |
|----------|-------|------------|
| `WORKFLOW_VERSION_INVALID_REQUEST` | WARNING | `WorkflowVersionService` rejected an `offset`/`limit`/`revision` argument before hitting the repository. Lets ops trace bad caller input separately from repository-layer failures. |

**Communication subscriber backpressure (`observability/events/communication.py`):**

| Constant | Level | When fired |
|----------|-------|------------|
| `COMM_SUBSCRIBER_QUEUE_OVERFLOW` | WARNING | A subscriber cannot keep up with inbound traffic. In-memory bus: the incoming envelope is dropped (`drop_policy=newest`). NATS: the pull consumer reached its `max_ack_pending` cap and JetStream is pausing delivery (`drop_policy=delivery_paused`). Fields: `channel`, `subscriber`, `queue_size`, `drop_policy`, `backend`, `num_ack_pending` (NATS only). NATS emissions are rate-limited to one per (channel, subscriber) pair per 60s to prevent log flooding (per-pair, not per-subscriber globally, so a subscriber overflowing on two channels still produces one warning per channel). |

**Telemetry collector lifecycle (`observability/events/telemetry.py`):**

The module carries two name-spaces: `TELEMETRY_*` constants are observability log events (emitted via `logger.*(...)`), `TELEMETRY_EVENT_*` constants are payload event types that ride inside `TelemetryEvent.event_type` through the privacy scrubber to the reporter.

| Constant | Level | When fired |
|----------|-------|------------|
| `TELEMETRY_ENABLED` | INFO | `start()` finished the deployment-ID lifecycle and the collector is live. The `deployment_id` carried here may be persisted on disk OR a generated UUID fallback if the load timed out / failed; the `using_generated_id` flag on the upstream `TELEMETRY_REPORT_FAILED` event distinguishes them. |
| `TELEMETRY_DISABLED` | DEBUG | Constructor saw `enabled=false`; the collector is inert (no on-disk trace). |
| `TELEMETRY_ENVIRONMENT_RESOLVED` | INFO | The four-level environment chain in `_resolve_environment` overrode the configured value. Carries both the configured and resolved tags. |
| `TELEMETRY_DEPLOYMENT_ID_LOADED` | INFO | Existing `telemetry_id` file read from disk during `start()` (or after a peer wrote first). Carries `deployment_id`. |
| `TELEMETRY_DEPLOYMENT_ID_CREATED` | INFO | This replica won the `O_CREAT\|O_EXCL` race and persisted a fresh UUID. Carries `deployment_id`. |
| `TELEMETRY_HEARTBEAT_SENT` | DEBUG | Heartbeat event delivered successfully. |
| `TELEMETRY_SESSION_SUMMARY_SENT` | DEBUG | Session summary delivered on shutdown. |
| `TELEMETRY_REPORT_FAILED` | WARNING | Privacy-scrubber rejection, reporter delivery error, snapshot-provider failure, or any deployment-ID lifecycle failure. The `detail` field is one of: `invalid_env_value`, `data_dir_not_trusted`, `deployment_id_read`, `deployment_id_write`, `deployment_id_invalid`, `deployment_id_peer_read`, `deployment_id_peer_file_deleted`, `deployment_id_peer_file_unreadable`, `deployment_id_peer_file_decode_error`, `deployment_id_peer_read_exhausted`, `deployment_id_load_timeout`, `deployment_id_load_unexpected_error`, `deployment_id_load_unexpected_helper_error`, `session_summary_snapshot_failed`, `send_session_summary_failed`, `send_shutdown_event_failed`, `reporter_shutdown_failed`. UUID-fallback paths carry `using_generated_id=True` so dashboards can detect splinter deployments. |
| `TELEMETRY_SHUTDOWN_WITHOUT_START` | WARNING | `shutdown()` invoked on an enabled collector that never had `start()` called (or whose `start()` failed before the deployment ID loaded). Surfaces silent init failure. |
| `TELEMETRY_CLOSED` | INFO | `shutdown()` flipped the collector into its terminal state. After this event a subsequent `start()` raises rather than silently reusing a torn-down reporter. |

<a id="audit-chain"></a>

### Audit chain

The `AuditChainSink` (`synthorg/observability/audit_chain/sink.py`) is
a stdlib `logging.Handler` that signs and chains a curated subset of
log events into a tamper-evident hash chain. Every appended entry is
ML-DSA-65-signed (or Ed25519-fallback) and timestamped through
`ResilientTimestampProvider` (TSA when reachable, local clock fallback).

#### Opt-in by prefix

The sink filters on a class-level allowlist:

```python
_AUDITED_PREFIXES = ("security.", "tool.registry.integrity.")
```

To make a new event reach the chain, name it `security.<domain>.<verb>`
(or `tool.registry.integrity.<...>`). Operational events keep their
existing namespace (`integrations.*`, `meta.*`, `api.*`, ...) and are
**not** signed. The `security` namespace is therefore the single
opt-in seam: a rename from `integrations.*` to `security.*` is the
only way to bring an event into the chain. Repository-layer
operational events MAY coexist with the security-namespace event for
the same lifecycle hop (e.g. `integrations.connection.created` at the
catalog layer and `security.connection.created` at the API controller
layer is the canonical two-layer pattern).

#### Record-shape extraction

`AuditChainSink.emit()` accepts log records from both stdlib
(`logging.getLogger(...).info("security.x.y")`) and the structlog
bridge (`synthorg.observability.get_logger`). Structlog routes records
through stdlib in two distinct shapes depending on whether
`ProcessorFormatter.wrap_for_formatter` has run:

- **stdlib direct**: `record.msg` is a `str`; the message IS the
  event name.
- **structlog pre-bridge**: `record.msg` is the event_dict
  (`{"event": "security.x.y", ...}`).
- **structlog post-bridge**: `record.msg` is a tuple
  `(event_dict, foreign_pre_chain)`; the event_dict is the first
  element.

The helper `_extract_event_name` in `audit_chain/sink.py` returns the
canonical event name from any of the three shapes, or `None` for any
unknown shape. An unknown shape raises a WARNING under the
non-recursive `audit_chain.record_shape_unknown` event so a future
logging-bridge change does not silently drop security events.

#### Failure modes

`emit()` distinguishes three failure paths so operators can triage:

| Event | When | Callback status |
|---|---|---|
| `audit_chain.emit_timeout` | Sign + TSA exceeded `audit_chain_signing_timeout_seconds` (default 5s) | `error` |
| `audit_chain.emit_error` | Any other exception (serialization, signer crash, ...) | `error` |
| `audit_chain.callback_error` | The append callback itself raised; chain still appended | (none) |

All three use the `audit_chain.*` prefix (NOT `security.*`) so the
diagnostic log can never recurse through `emit()` and deadlock the
single-worker signing executor.

### Uvicorn Integration

Uvicorn's default access logger is **disabled** (`access_log=False`, `log_config=None`).
HTTP access logging is handled by `RequestLoggingMiddleware`, which provides richer structured
fields (method, path, status_code, duration_ms, request_id) through structlog. Uvicorn's own
handlers are cleared by `_tame_third_party_loggers()` and its loggers (`uvicorn`,
`uvicorn.error`, `uvicorn.access`) are set to `WARNING` with `propagate = True`; startup
INFO messages (e.g., "Uvicorn running on ...") are intentionally suppressed since the
application's own lifecycle logging provides equivalent structured events via structlog.
Warning and error messages still propagate through the structlog pipeline.

### Litestar Integration

Litestar's built-in logging configuration is **disabled** (`logging_config=None` in the
`Litestar()` constructor). Without this, Litestar reconfigures stdlib's root handler on
startup via `dictConfig()`, which triggers `_clearExistingHandlers` and destroys the structlog
file sink handlers attached by `_bootstrap_app_logging()`. The bootstrap call in `create_app`
runs before the Litestar constructor and sets up all 11 sinks; `logging_config=None` ensures
they survive.

### Third-Party Logger Taming

LiteLLM and its HTTP stack (httpx, httpcore) attach their own `StreamHandler` instances at
import time, producing duplicate output in Docker logs: once via the library's own handler,
and once again via root propagation through the structlog sinks.

`_tame_third_party_loggers()` (called as step 7 of `configure_logging`, before per-logger level
overrides so explicit user settings take precedence) resolves this by:

- Suppressing LiteLLM's raw `print()` output via `litellm.set_verbose = False` and
  `litellm.suppress_debug_info = True` (applied only when `litellm` is already imported,
  to avoid triggering LiteLLM's expensive import side-effects)
- Clearing all handlers from `LiteLLM`, `LiteLLM Router`, `LiteLLM Proxy`, `aiosqlite`,
  `httpcore`, `httpcore.http11`, `httpcore.connection`, `httpx`, `uvicorn`, `uvicorn.error`,
  `uvicorn.access`, `anyio`, `multipart`, `faker`, and `faker.factory` loggers
- Setting each to `WARNING` and `propagate = True` so warnings and errors still flow through
  the structlog pipeline

The provider and persistence layers already log meaningful events at appropriate levels via
their own structlog calls; the third-party loggers would otherwise add noisy DEBUG output
that duplicates or contradicts those structured events.

### Docker Logging

Two layers of log management:

1. **App-level** (structlog): 11 sinks (10 file + 1 console). File sinks use `RotatingFileHandler`
   (10 MB x 5) writing JSON to `/data/logs/`. Console sink writes colored text to stderr.
2. **Container-level** (Docker): `json-file` driver with 10 MB x 3 rotation on
   stdout/stderr. Captures console sink output and any uncaught stderr.

The layers are complementary: app files provide structured, routed logs; Docker captures
the console stream for `docker logs` access.

### Runtime Settings

Four observability settings are runtime-editable via `SettingsService`:

- `root_log_level` (enum: debug/info/warning/error/critical): changes the root logger level
- `enable_correlation` (boolean): toggles correlation ID injection
- `sink_overrides` (JSON): per-sink overrides keyed by sink identifier (`__console__` for the
  console sink, file path for file sinks). Each value is an object with optional fields:
  `enabled` (bool), `level` (string), `json_format` (bool), `rotation` (object with `max_bytes`,
  `backup_count`, `strategy`, `compress_rotated` (built-in-only)). The console sink cannot be disabled
  (`enabled: false` is rejected).
- `custom_sinks` (JSON): additional sinks as a JSON array. Each entry may specify `sink_type`
  (`file`, `syslog`, `http`; defaults to `file`). File sinks require `file_path` and accept
  `level`, `json_format`, `rotation`, `routing_prefixes`. Syslog sinks require `syslog_host`
  and accept `syslog_port`, `syslog_facility`, `syslog_protocol`, `level`. HTTP sinks require
  `http_url` and accept `http_headers`, `http_batch_size`, `http_flush_interval_seconds`,
  `http_timeout_seconds`, `http_max_retries`, `level`.

Console sink level can also be overridden via `SYNTHORG_LOG_LEVEL` env var.

Changes take effect without restart; the `ObservabilitySettingsSubscriber` rebuilds the entire
logging pipeline via `configure_logging()` (idempotent) when any of the four observability
settings change (`root_log_level`, `enable_correlation`, `sink_overrides`, or `custom_sinks`).
Custom sink file paths cannot collide with default sink paths (reserved even if disabled).

---

## Prometheus Metrics Inventory

The `/metrics` endpoint exposes business and infrastructure metrics under the `synthorg_` prefix. Canonical set maintained by `observability/prometheus_collector.py` + `observability/prometheus_push_metrics.py`. All label value sets are bounded (validated against `prometheus_labels` allowlists) to keep Prometheus TSDB cardinality predictable.

**Business health**

- `synthorg_escalation_queue_depth{department}`: gauge; pending escalations awaiting decision, per department.
- `synthorg_agent_identity_version_changes_total{agent_id, change_type}`: counter; emitted on each agent identity change. `change_type` is one of `created`, `updated`, `rolled_back`, `archived`.
- `synthorg_workflow_execution_seconds{workflow_definition_id, status}`: histogram; wall-clock duration of completed workflow executions. `workflow_definition_id` is the stable workflow **definition** id (bounded by defined workflows); passing an execution id would explode cardinality.

**Client transport**

- `synthorg_client_disconnects_total{transport, reason}`: counter; emitted from SSE / WebSocket / MCP disconnect handlers. `transport` ∈ {`sse`, `websocket`, `mcp_stdio`, `mcp_http`} (the two `mcp_*` values are emitted by `synthorg.tools.mcp.client` for stdio and streamable-HTTP MCP transports respectively); `reason` ∈ {`client_initiated`, `transport_error`, `cancelled`, `timeout`}. Bounded labels keep cardinality at 16 series (4 transports × 4 reasons), matching `VALID_DISCONNECT_TRANSPORTS` / `VALID_DISCONNECT_REASONS` in `prometheus_labels.py`.

**Snapshot-backed registry-bound labels.** Four push-time label names (`agent_id`, `agent`, `department`, `workflow_definition_id`) are validated against a process-global `_LabelSnapshot` rebuilt on every async pre-scrape `PrometheusCollector.refresh()`. Sync `record_*` callers consult the snapshot via `validate_<label>` / `is_known_agent_id`; unknown values drop the sample with a `metrics.scrape.failed` WARN log (and `metrics.record.failed` at the metrics-hub wrapper level). Concurrency is guaranteed by atomic module-global rebinding (single bytecode op under the GIL) plus a capture-before-read pattern in every validator: the validator reads the module global once into a local before consulting the per-source `*_seeded` flags and the frozenset, so a concurrent `update_label_snapshot()` either swaps in the new snapshot before the local capture or after it -- never producing a torn `(*_seeded, frozenset)` pair. The collector additionally serializes the read/merge/write critical section in `_rebuild_label_snapshot` with a per-instance `asyncio.Lock` so two overlapping `refresh()` calls cannot clobber a partial-failure carry-forward. See `src/synthorg/observability/prometheus_labels.py`.

**Cost + tokens**

- `synthorg_provider_tokens_total{provider, model, direction}`: counter; input/output token consumption.
- `synthorg_provider_cost_total{provider, model}`: counter; accumulated cost in the configured currency.

**Provider errors**

- `synthorg_provider_errors_total{provider, model, error_class}`: counter; emitted from every failed `BaseCompletionProvider.complete` / `stream` call. `error_class` is one of the bounded `ProviderErrorLabel` values (`rate_limit`, `timeout`, `connection`, `internal`, `invalid_request`, `auth`, `content_filter`, `not_found`, `other`) produced by `classify_provider_error`.

**Caches**

- `synthorg_cache_operations_total{cache_name, outcome}`: counter; emitted from the in-process caches (`mcp_result`, `reranker`). `outcome` is one of `hit` / `miss` / `evict`.

**Latency**

- `synthorg_api_request_duration_seconds{method, route, status_class}`: histogram; per-route HTTP handler duration. Its auto-emitted `_count` series doubles as a request counter.
- `synthorg_task_duration_seconds{outcome}` + `synthorg_task_runs_total{outcome}`: task execution.
- `synthorg_tool_duration_seconds{tool_name, outcome}` + `synthorg_tool_invocations_total{tool_name, outcome}`: tool invocation.

**API errors**

- `synthorg_api_error_classification_total{category, status_class}`: counter; emitted from the structured-error builder on every 4xx/5xx response. `category` is derived from the `ErrorCategory` enum (`auth`, `validation`, `not_found`, `conflict`, `rate_limit`, `budget_exhausted`, `provider_error`, `internal`) with no parallel allowlist.

**Audit chain + OTLP health**

- `synthorg_audit_chain_appends_total{status}`, `synthorg_audit_chain_depth`, `synthorg_audit_chain_last_append_timestamp_seconds`.
- `synthorg_otlp_export_batches_total{kind, outcome}`, `synthorg_otlp_export_dropped_records_total{kind}`.

See the ready-to-import [Grafana dashboard](../../monitoring/grafana/synthorg-overview.json) and the [monitoring guide](../guides/monitoring.md) for PromQL queries, alert rules, and expected ranges for each metric.

---

## See Also

- [Notifications](notifications.md): notification dispatcher and sinks
- [Design Overview](index.md): full index
