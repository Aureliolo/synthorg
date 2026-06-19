---
title: Monitoring & Dashboards
description: Prometheus metric inventory, suggested PromQL queries, Grafana dashboard import, and Logfire integration notes.
---

# Monitoring & Dashboards

SynthOrg exposes runtime telemetry via a Prometheus `/metrics` endpoint plus structured JSON logs. This guide walks through every metric the application emits, a ready-to-import Grafana dashboard, and suggested alert rules. The canonical metric registration lives in `src/synthorg/observability/prometheus_collector.py` (pull-refreshed families) and `src/synthorg/observability/prometheus_push_metrics.py` (push-updated families); bounded label allowlists live in `src/synthorg/observability/prometheus_labels.py`.

## Scraping

Point any Prometheus-compatible scraper at the running app:

```yaml
scrape_configs:
  - job_name: synthorg
    scrape_interval: 30s
    static_configs:
      - targets: ['synthorg:8000']
```

The endpoint is unauthenticated by default; put it behind your normal scrape-ACL (firewall, sidecar proxy, Kubernetes NetworkPolicy). All metric names are prefixed with `synthorg_`.

## Metric inventory

The **Dashboard** column maps each metric to a row in the default Grafana overview dashboard (`monitoring/grafana/synthorg-overview.json`). Rows are collapsible; the only row expanded by default is `Health & SLO`. The dashboard exposes four filter variables (`$agent_id`, `$agent`, `$workflow_definition_id`, `$department`) that drill panels down per-entity; the two agent-named variables exist because `synthorg_tasks_total` uses `agent` while the per-agent cost metrics use `agent_id`. Default queries aggregate across the full set so the unfiltered view is always meaningful.

Bounded-label values are enforced at record time in `src/synthorg/observability/prometheus_labels.py`; PromQL filters that reference values outside those allowlists will never match data. The five registry-bound push-time labels (`agent_id`, `agent`, `department`, `workflow_definition_id`, `tool_name` on the metrics noted below) are validated against a registry-bound snapshot rebuilt on every Prometheus scrape; unknown values drop that one sample with a `metrics.scrape.failed` WARN log per unknown label per scrape. The log repeats on the next scrape if the value is still unknown.

### Info

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_app_info` | Info | `version` | Application build info. `prometheus_client` appends the `_info` suffix, so the scraped series is `synthorg_app_info` (not `synthorg_app`). | `Client Health` |

### Coordination (push-updated per multi-agent run)

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_coordination_efficiency` | Gauge | - | 0.0-1.0 efficiency ratio. | `Health & SLO` |
| `synthorg_coordination_overhead_percent` | Gauge | - | % of wall time spent coordinating. | `Health & SLO` |

### Cost & budget (pull-refreshed at scrape)

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_cost_total` | Gauge | - | Total accumulated cost. | `Cost & Budget` |
| `synthorg_budget_used_percent` | Gauge | - | Monthly budget utilisation. | `Health & SLO` |
| `synthorg_budget_monthly_cost` | Gauge | - | Monthly budget in configured currency. | `Cost & Budget` |
| `synthorg_budget_daily_used_percent` | Gauge | - | Daily utilisation (prorated). | `Cost & Budget` |
| `synthorg_agent_cost_total` | Gauge | `agent_id` (registry-bound) | Per-agent accumulated cost. | `Cost & Budget` |
| `synthorg_agent_budget_used_percent` | Gauge | `agent_id` (registry-bound) | Per-agent daily utilisation. | `Cost & Budget` |
| `synthorg_budget_query_duration_seconds` | Histogram | `query_type` | Budget read-path query duration (`query_type` bounded to `balance` / `available_spend` / `burn_rate` / `daily_spend` / `cost_summary` / `total_cost` / `agent_cost` / `project_cost`; buckets 1ms-1s). | `Audit & Performance` |

### Agents & tasks

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_active_agents_total` | Gauge | `status`, `trust_level` | Active agent count by status. | `Health & SLO` |
| `synthorg_tasks_total` | Gauge | `status`, `agent` (registry-bound) | Task count per status per agent. | `Tasks` |
| `synthorg_task_runs_total` | Counter | `outcome` | Emitted task outcomes by bounded `outcome` (`succeeded` / `failed` / `cancelled` / `rejected`). One increment per terminal-status hop on a task; a task that transitions through `failed` and is later retried therefore counts as one `failed` *and* one `succeeded` (or another terminal value) -- the counter records emitted outcomes, not unique task ids. | `Tasks` |
| `synthorg_task_duration_seconds` | Histogram | `outcome` | Task execution duration in seconds, partitioned by the same `outcome` values as `synthorg_task_runs_total` (buckets 0.1s-600s). Observed only when the engine has a recorded creation timestamp; transitions where the timestamp is unavailable (e.g. a task created before a process restart) skip the histogram and emit `task_engine.timing_fallback` WARN with `synthorg_task_runs_total` still incremented so the count and histogram percentages remain comparable. | `Tasks` |

### Providers

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_provider_tokens_total` | Counter | `provider`, `model`, `direction` | Input/output tokens by model (`direction` bounded to `input`/`output`). | `Tools & Providers` |
| `synthorg_provider_cost_total` | Counter | `provider`, `model` | Cost per provider call. | `Tools & Providers` |
| `synthorg_provider_errors_total` | Counter | `provider`, `model`, `error_class` | Provider-call failures classified by `rate_limit` / `timeout` / `connection` / `internal` / `invalid_request` / `auth` / `content_filter` / `not_found` / `other`. | `Tools & Providers` |
| `synthorg_provider_call_duration_seconds` | Histogram | `provider`, `model`, `call_type` | Provider call wall-clock duration per provider, model, and call type (buckets 0.05s-120s). The auto-emitted `_count` series is the per-label call counter. | `Tools & Providers` |

### Tools

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_tool_invocations_total` | Counter | `tool_name`, `outcome` | Tool invocations by bounded outcome (`success` / `error` / `timeout`). | `Tools & Providers` |
| `synthorg_tool_duration_seconds` | Histogram | `tool_name`, `outcome` | Tool invocation duration (buckets 5ms-120s). | `Tools & Providers` |

### API

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_api_request_duration_seconds` | Histogram | `method`, `route`, `status_class` | HTTP request handler duration (buckets 5ms-10s). The auto-emitted `_count` series is the per-label request counter; use it for request-rate PromQL. | `Client Health` |
| `synthorg_api_error_classification_total` | Counter | `category`, `status_class` | 4xx/5xx response counter partitioned by RFC 9457 category (`auth` / `validation` / `not_found` / `conflict` / `rate_limit` / `budget_exhausted` / `provider_error` / `internal`) and status class. | `Audit & Security` |

### Caches

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_cache_operations_total` | Counter | `cache_name`, `outcome` | In-process cache operations (`cache_name` bounded to `mcp_result` / `reranker`; `outcome` bounded to `hit` / `miss` / `evict`). | `Client Health` |

### Security

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_security_evaluations_total` | Counter | `verdict` | Pre-tool security verdicts (`verdict` bounded to `allow` / `deny` / `escalate` / `output_scan`). | `Audit & Security` |

### Audit chain

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_audit_chain_appends_total` | Counter | `status` | Audit chain append operations (`status` bounded to `signed` / `fallback` / `error`). | `Audit & Security` |
| `synthorg_audit_chain_verifications_total` | Counter | `outcome` | Audit chain integrity verifications (`outcome` bounded to `valid` / `broken`); a non-zero `broken` rate is the chain-tampering alert signal. | `Audit & Performance` |
| `synthorg_audit_chain_depth` | Gauge | - | Current hash chain length. | `Audit & Security` |
| `synthorg_audit_chain_last_append_timestamp_seconds` | Gauge | - | Unix timestamp of the most recent append. | `Audit & Security` |
| `synthorg_security_audit_log_fill_ratio` | Gauge | - | Security audit log occupancy as a fraction of `max_entries` (0.0 empty, 1.0 full). Alert at 0.9: increase retention or archive older entries before the ring buffer wraps and overwrites unread evidence. | `Audit & Security` |

### OTLP export health

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_otlp_export_batches_total` | Counter | `kind`, `outcome` | Export batches by kind (`logs` / `traces`) and outcome (`success` / `failure`). | `Client Health` |
| `synthorg_otlp_export_dropped_records_total` | Counter | `kind` | Records dropped because the queue was full or the retry budget exhausted. | `Client Health` |

### Client transport

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_client_disconnects_total` | Counter | `transport`, `reason` | Client transport disconnections (`transport` bounded to `sse` / `websocket` / `mcp_stdio` / `mcp_http`; `reason` bounded to `client_initiated` / `transport_error` / `cancelled` / `timeout`). | `Client Health` |

### WebSocket transport

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_ws_active_connections` | Gauge | (none) | Currently-open WebSocket connections. | `WebSocket Transport` |
| `synthorg_ws_connection_lifetime_seconds` | Histogram | `transport` | WebSocket connection lifetime by transport (`transport` bounded to `websocket` / `sse`; buckets 1s-4h). A collapsing p95 flags clients dropping shortly after auth. | `WebSocket Transport` |
| `synthorg_ws_revalidation_total` | Counter | `outcome` | Per-frame WS revalidation outcomes (`outcome` bounded to `pass` / `fail` / `budget_exhausted`). A sustained `budget_exhausted` rate is the revalidation-saturation signal (saturated peers close with 4011). | `WebSocket Transport` |

### Database connection pool

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_pg_pool_size` | Gauge | `backend` | Configured Postgres pool size (`backend` bounded to `primary` / `replica`). | `Database Connection Pool` |
| `synthorg_pg_pool_active_connections` | Gauge | `backend` | Connections currently checked out of the pool. Approaching `synthorg_pg_pool_size` is the saturation precursor. | `Database Connection Pool` |
| `synthorg_pg_pool_acquire_duration_seconds` | Histogram | `backend` | Wall time spent waiting for a connection (buckets 1ms-5s). Rising acquire latency precedes exhaustion. | `Database Connection Pool` |
| `synthorg_pg_pool_exhausted_total` | Counter | `backend` | Pool-acquisition timeouts (no connection available). Any non-zero rate is alertable. | `Database Connection Pool` |

### Push queue + log shipping

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_push_queue_events_total` | Counter | `outcome` | Workspace merge+push queue events (`outcome` bounded to `enqueued` / `merged`). A growing gap between `enqueued` and `merged` means the queue is backing up. | `Log Shipping & Queues` |
| `synthorg_log_sink_events_total` | Counter | `sink`, `outcome` | HTTP / syslog log-shipping sink export outcomes (`sink` bounded to `http` / `syslog`; `outcome` bounded to `success` / `failure`). A sustained `failure` rate means a misconfigured or unreachable shipping endpoint is dropping records. | `Log Shipping & Queues` |

### Escalation + identity + workflow

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_escalation_queue_depth` | Gauge | `department` (registry-bound) | Pending escalations awaiting decision. | `Health & SLO` |
| `synthorg_agent_identity_version_changes_total` | Counter | `agent_id` (registry-bound), `change_type` | Identity-version lifecycle events (`change_type` bounded to `created` / `updated` / `rolled_back` / `archived`). | `Audit & Security` |
| `synthorg_workflow_execution_seconds` | Histogram | `workflow_definition_id` (registry-bound), `status` | Workflow execution duration (`status` bounded to `completed` / `failed` / `cancelled` / `timeout`; buckets 0.5s-3600s). | `Workflows` |

### Decisions

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_approval_decisions_total` | Counter | `outcome` | Approval-gate terminal decisions (`outcome` bounded to `approved` / `rejected` / `expired`). | `Decisions` |
| `synthorg_escalation_outcomes_total` | Counter | `outcome` | Conflict-resolution escalation terminal outcomes (`outcome` bounded to `resolved` / `escalated_to_human` / `auto_resolved` / `notify_failed` / `sweeper_failed`). | `Decisions` |
| `synthorg_blueprint_instantiations_total` | Counter | `outcome` | Workflow blueprint instantiation attempts (`outcome` bounded to `success` / `validation_error` / `not_found` / `unknown_error`). | `Decisions` |
| `synthorg_autonomy_promotion_decisions_total` | Counter | `outcome` | Autonomy-promotion workflow terminal decisions by bounded `outcome` (`granted` / `denied`). | `Decisions` |

### Configuration & MCP

| Metric | Type | Labels | Description | Dashboard |
|--------|------|--------|-------------|-----------|
| `synthorg_settings_mutations_total` | Counter | `namespace` | Settings mutations by namespace (`namespace` bounded to the settings-namespace allowlist, one entry per `settings/definitions/` file). | `Configuration & MCP` |
| `synthorg_mcp_handler_outcomes_total` | Counter | `tool`, `outcome` | MCP handler invocations by tool (`outcome` bounded to `success` / `error` / `validation_error` / `guardrail_violated` / `not_found` / `capability_unsupported`). | `Configuration & MCP` |
| `synthorg_mcp_handler_duration_seconds` | Histogram | `tool`, `outcome` | MCP handler invocation duration by tool and outcome (buckets 1ms-10s). | `Configuration & MCP` |

## Suggested PromQL queries

### Saturation / backlog

```promql
# Escalation backlog (any department) sustained above 5 for 10m
max_over_time(synthorg_escalation_queue_depth[10m]) > 5

# Workflow p95 latency exceeds 60s
histogram_quantile(0.95, sum by (le) (rate(synthorg_workflow_execution_seconds_bucket[5m]))) > 60
```

### Cost / budget

```promql
# Burned 80% of the monthly budget
synthorg_budget_used_percent > 80

# Per-agent cost top 5 (most expensive right now)
topk(5, synthorg_agent_cost_total)
```

### Coordination health

```promql
# Coordination overhead sustained above 40% for 10 minutes
avg_over_time(synthorg_coordination_overhead_percent[10m]) > 40

# Coordination efficiency dropped below 0.5 (half of runs wasted)
avg_over_time(synthorg_coordination_efficiency[15m]) < 0.5
```

### Identity lifecycle

```promql
# Rollback rate over the last hour (audit-relevant spike check)
sum(rate(synthorg_agent_identity_version_changes_total{change_type="rolled_back"}[1h]))

# Churn rate -- identity updates per minute
sum by (change_type) (rate(synthorg_agent_identity_version_changes_total[5m]))
```

### API health

```promql
# 5xx rate as a fraction of total (clamp_min avoids NaN/Inf in idle windows)
sum(rate(synthorg_api_request_duration_seconds_count{status_class="5xx"}[5m]))
  / clamp_min(sum(rate(synthorg_api_request_duration_seconds_count[5m])), 1)

# Request rate by status class (histogram's auto-emitted _count series)
sum by (status_class) (rate(synthorg_api_request_duration_seconds_count[1m]))

# Error rate by RFC 9457 category
sum by (category) (rate(synthorg_api_error_classification_total[5m]))

# 5xx rate by category (internal vs rate_limit vs provider_error, etc.)
sum by (category) (rate(synthorg_api_error_classification_total{status_class="5xx"}[5m]))
```

### Provider health

```promql
# Provider error rate per class (hot loop: rate_limit + timeout + connection)
sum by (provider, error_class) (rate(synthorg_provider_errors_total[5m]))

# Token-normalized provider error rate (error events per token volume)
sum by (provider) (rate(synthorg_provider_errors_total[5m]))
  / clamp_min(sum by (provider) (rate(synthorg_provider_tokens_total[5m])), 1)

# Provider call latency p95 by provider + call type
histogram_quantile(
  0.95,
  sum by (le, provider, call_type) (
    rate(synthorg_provider_call_duration_seconds_bucket[5m])
  )
)

# Provider call rate (the histogram's auto-emitted _count series)
sum by (provider, call_type) (rate(synthorg_provider_call_duration_seconds_count[5m]))
```

### Cache hit rate

```promql
# Hit rate per cache (0.0-1.0)
sum by (cache_name) (rate(synthorg_cache_operations_total{outcome="hit"}[5m]))
  / clamp_min(sum by (cache_name) (rate(synthorg_cache_operations_total[5m])), 1)

# Eviction spike (may indicate undersized cache)
sum by (cache_name) (rate(synthorg_cache_operations_total{outcome="evict"}[5m]))
```

### Autonomy promotion

```promql
# Promotion grant vs deny rate (governance throughput by outcome)
sum by (outcome) (rate(synthorg_autonomy_promotion_decisions_total[1h]))

# Denial fraction of all promotion decisions over the last day
sum(rate(synthorg_autonomy_promotion_decisions_total{outcome="denied"}[1d]))
  / clamp_min(sum(rate(synthorg_autonomy_promotion_decisions_total[1d])), 1)
```

### Security posture

```promql
# Denial rate (should be low; spike indicates policy tightening or attack)
rate(synthorg_security_evaluations_total{verdict="deny"}[5m])

# Escalation rate per minute
rate(synthorg_security_evaluations_total{verdict="escalate"}[1m])
```

### Audit chain health

```promql
# Append-error rate (non-zero = signing pipeline is broken)
rate(synthorg_audit_chain_appends_total{status="error"}[5m])

# Seconds since last append (flat line for > 5m is suspicious)
time() - synthorg_audit_chain_last_append_timestamp_seconds

# Audit log fill ratio (alert when the ring buffer is near capacity).
# At >0.9 the next bursts of activity overwrite the oldest entries
# before an operator can read them; rotate retention or archive.
synthorg_security_audit_log_fill_ratio
```

### Audit log fill ratio

The `synthorg_security_audit_log_fill_ratio` gauge reports the
occupancy of the in-memory security audit log as a fraction of its
configured `max_entries` capacity. The log is a ring buffer: once
full, the oldest entries are overwritten as new audit events land.
A sustained value above 0.9 means the buffer is about to wrap; any
unread evidence beyond that point is permanently lost.

Recommended alert rule:

```yaml
- alert: SynthorgSecurityAuditLogNearCapacity
  expr: synthorg_security_audit_log_fill_ratio > 0.9
  for: 10m
  labels: {severity: warning}
  annotations:
    summary: "Security audit log is {{ $value | humanizePercentage }} full"
    runbook: "increase max_entries, archive entries to long-term storage, or shorten retention"
```

Grafana panel definition (drop into the `Audit & Security` row of
`monitoring/grafana/synthorg-overview.json`):

```json
{
  "title": "Security audit log fill ratio",
  "type": "gauge",
  "datasource": "${DS_PROMETHEUS}",
  "fieldConfig": {
    "defaults": {
      "min": 0,
      "max": 1,
      "unit": "percentunit",
      "thresholds": {
        "mode": "absolute",
        "steps": [
          {"color": "green", "value": null},
          {"color": "yellow", "value": 0.75},
          {"color": "red", "value": 0.9}
        ]
      }
    }
  },
  "targets": [
    {"expr": "synthorg_security_audit_log_fill_ratio", "refId": "A"}
  ]
}
```

### OTLP export health

```promql
# Export-failure rate per kind
sum by (kind) (rate(synthorg_otlp_export_batches_total{outcome="failure"}[5m]))

# Dropped records per kind (queue overflow or retries exhausted)
sum by (kind) (rate(synthorg_otlp_export_dropped_records_total[5m]))
```

### Client transport health

```promql
# Disconnect rate by transport (alert on transport_error spikes)
sum by (transport, reason) (rate(synthorg_client_disconnects_total[5m]))

# Transport-error rate as a fraction of all disconnects
sum(rate(synthorg_client_disconnects_total{reason="transport_error"}[5m]))
  / clamp_min(sum(rate(synthorg_client_disconnects_total[5m])), 1)
```

## Grafana dashboard

Import `monitoring/grafana/synthorg-overview.json` into any Grafana v10+ instance. The file is Grafana v10-compatible dashboard JSON (authored against the v11 editor, which emits a schema readable by v10) with a single `${DS_PROMETHEUS}` template variable bound to your Prometheus data source plus four filter variables: `$agent_id` (sourced from `synthorg_agent_cost_total`'s `agent_id` label, used by Cost & Budget + Audit & Security panels), `$agent` (sourced from `synthorg_tasks_total`'s `agent` label, used by the Tasks row's per-agent panel), `$workflow_definition_id`, and `$department`. The two agent-named variables exist because `synthorg_tasks_total` and `synthorg_agent_cost_total` use different label names (`agent` vs `agent_id`); panels filter on whichever variable matches their underlying metric.

The dashboard organises over fifty panels into thirteen rows. Only `Health & SLO` is expanded by default; expand the others as needed to keep the unfiltered view scannable.

| Row | Default | Panels |
|-----|---------|--------|
| `Health & SLO` | expanded | Coordination efficiency, coordination overhead, budget utilisation, active agents, escalation queue depth |
| `Tasks` | collapsed | Task completion rate, task duration p50/p95, tasks-by-status, task-runs-by-outcome, tasks per agent |
| `Workflows` | collapsed | Workflow duration p50/p95, workflow execution rate by status, top-N workflow definitions |
| `Tools & Providers` | collapsed | Tool invocation rate, tool duration p95 by `tool_name`, provider tokens, provider cost, provider errors by class, provider call latency p95 (`synthorg_provider_call_duration_seconds`) |
| `Cost & Budget` | collapsed | `synthorg_cost_total`, monthly cost, daily used %, top-25 per-agent cost, agent budget used % |
| `Audit & Security` | collapsed | Audit chain append rate, depth, last-append age, audit-log fill-ratio gauge, security verdicts, agent identity version changes, API error categories |
| `Client Health` | collapsed | Client disconnects by transport+reason, API request rate by status class, OTLP export batches, OTLP dropped records, cache hit rate, app info |
| `Decisions` | collapsed | Approval decisions/sec (`synthorg_approval_decisions_total`), escalation outcomes/sec (`synthorg_escalation_outcomes_total`), blueprint instantiations/sec (`synthorg_blueprint_instantiations_total`), autonomy promotion decisions/sec (`synthorg_autonomy_promotion_decisions_total`) |
| `Configuration & MCP` | collapsed | Settings mutations/sec by namespace (`synthorg_settings_mutations_total`), MCP handler success rate (`synthorg_mcp_handler_outcomes_total`), MCP handler p95 latency by tool (`synthorg_mcp_handler_duration_seconds`) |
| `Audit & Performance` | collapsed | Audit chain signing-error rate (`synthorg_audit_chain_appends_total{status="error"}`), audit chain integrity over the last hour (`synthorg_audit_chain_verifications_total`), budget query p95 latency by query type (`synthorg_budget_query_duration_seconds`) |
| `WebSocket Transport` | collapsed | Active connections (`synthorg_ws_active_connections`), revalidation outcomes (`synthorg_ws_revalidation_total`), connection lifetime p95 by transport (`synthorg_ws_connection_lifetime_seconds`) |
| `Database Connection Pool` | collapsed | Pool size + active connections by backend (`synthorg_pg_pool_size` / `synthorg_pg_pool_active_connections`), acquire p95 (`synthorg_pg_pool_acquire_duration_seconds`), exhaustion rate (`synthorg_pg_pool_exhausted_total`) |
| `Log Shipping & Queues` | collapsed | Workspace push-queue events (`synthorg_push_queue_events_total`), HTTP / syslog sink outcomes (`synthorg_log_sink_events_total`) |

To install via the Grafana UI: `Dashboards → New → Import → Upload JSON file`. Via the provisioning API: `POST /api/dashboards/db` with `{"dashboard": <file>, "overwrite": true, "inputs": [...]}`.

## Alerts

The file does not ship alert rules because thresholds are deployment-specific. The suggested PromQL above is ready to drop into Prometheus' `rules.yml`; pair each query with a `labels: severity: warning|critical` and a `for:` duration. Example:

```yaml
groups:
  - name: synthorg
    rules:
      - alert: SynthorgCoordinationOverheadHigh
        expr: avg_over_time(synthorg_coordination_overhead_percent[10m]) > 40
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "Coordination overhead is {{ $value }}%"
          runbook: "https://synthorg.io/docs/runbooks/coordination-overhead"
```

## Logfire

Logfire's Prometheus integration can scrape the same `/metrics` endpoint directly; no additional wiring is required on the SynthOrg side. Follow the [Logfire documentation](https://pydantic.dev/docs/logfire/) for the Prometheus setup and point it at `http://synthorg:8000/metrics`. All metrics documented above will appear under the same names in Logfire dashboards.

## Further reading

- [Observability design](../design/observability.md): sink layout, correlation IDs, per-domain routing
- [Reference: errors](../reference/errors.md): RFC 9457 error categories
- `src/synthorg/observability/prometheus_collector.py`: canonical metric registration
- `src/synthorg/observability/prometheus_push_metrics.py`: push-updated metric families
- `src/synthorg/observability/prometheus_labels.py`: bounded label value sets
