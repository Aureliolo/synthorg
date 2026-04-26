---
title: "SynthOrg as Agent Control Plane: API Surface Audit and Gap Analysis"
issue: 688
source: "https://thenewstack.io/agentic-ai-control-plane-production/"
date: 2026-04-07
---

# SynthOrg as Agent Control Plane: API Surface Audit and Gap Analysis

## Context

The New Stack article "Agentic AI Control Plane: What It Needs in Production" argues that
enterprise agentic AI requires a dedicated control-plane layer (a system that inventories
agents, enforces behavioral policies at runtime, provides token metering and cost tracking,
and delivers end-to-end observability). The article references Galileo Agent Control as an
open-source example and frames this as a distinct architectural tier above individual agents.

**SynthOrg is already building this control plane.** The HR module handles agent inventory,
the security module enforces runtime policies, the budget module provides token metering, and
the observability module delivers structured telemetry. But we do not frame it as one
coherent layer, and we may have API surface gaps that prevent external consumers from
querying the right primitives.

This audit maps SynthOrg modules to control-plane primitives, identifies gaps between
internal capability and external API surface, validates the "write policies once, enforce
everywhere" claim, and draws positioning implications.

---

## Control-Plane Primitive Mapping

### Primitive 1: Agent Inventory

**What a control plane needs**: A queryable registry of all agents (their identity,
capabilities, status, and lifecycle state).

**SynthOrg implementation**:

- `src/synthorg/hr/registry.py` defines `AgentRegistryService`: in-memory agent identity store
  with register, get, update, list, and remove operations.
- `src/synthorg/hr/hiring_service.py`: pipeline from request through approval to
  instantiation, creating agents with full `AgentIdentity` (role, personality, skills, model
  config, tool permissions, authority, autonomy level).
- `src/synthorg/hr/performance/tracker.py` defines `PerformanceTracker`: per-agent rolling
  windows (7d, 30d, 90d), Theil-Sen trend detection, quality and collaboration scoring.
- `src/synthorg/hr/evaluation/evaluator.py` defines `EvaluationService`: 5-pillar composite
  scoring (intelligence, efficiency, resilience, governance, experience).

**API surface**:

| Operation | Endpoint | Notes |
|---|---|---|
| List agents | `GET /agents` | Paginated; returns AgentIdentity fields |
| Get agent | `GET /agents/{id}` | Full identity including model, tools, autonomy |
| Create agent | `POST /agents` | Requires write access |
| Update agent | `PATCH /agents/{id}` | Write access |
| Delete agent | `DELETE /agents/{id}` | Write access |
| Get autonomy | `GET /agents/{id}/autonomy` | Per-agent autonomy config |
| Set autonomy | `PUT /agents/{id}/autonomy` | Write access |
| List departments | `GET /departments` | Includes agent membership |
| Department health | `GET /departments/{name}/health` | Aggregated metrics |

**Coverage**: Agent enumeration and CRUD are well-covered. Lifecycle state (ACTIVE,
ONBOARDING, ON_LEAVE, TERMINATED) is part of `AgentIdentity.status` and returned by
`GET /agents/{id}`.

**Gap G2**: No `GET /agents/{id}/health` endpoint exposing per-agent composite health
(liveness + last-active timestamp + performance score + trust level). The performance data
exists in `hr/performance/tracker.py` but is only surfaced at the department level via
`GET /departments/{name}/health`, not per-agent. External control-plane consumers cannot
query a single agent's operational health without reading the full department.

---

### Primitive 2: Runtime Policy Enforcement

**What a control plane needs**: Policies expressible as code, applied uniformly across all
agents at runtime, without per-agent configuration.

**SynthOrg implementation**:

- `src/synthorg/security/service.py` defines `SecOpsService`: meta-agent coordinating evaluation.
  Takes `SecurityConfig` at construction; not a hot-reload singleton.
- `src/synthorg/security/rules/engine.py` defines `RuleEngine`: sequential rule evaluation with
  fail-closed semantics (first DENY/ESCALATE wins; errors return DENY with CRITICAL risk).
- `src/synthorg/security/rules/` hosts 5 built-in detectors: `PolicyValidator` (fast-path
  hard-deny and auto-approve frozensets), `CredentialDetector`, `PathTraversalDetector`,
  `DestructiveOpDetector`, `DataLeakDetector`. Plus `CustomPolicyRule` for user-defined rules.
- `src/synthorg/security/autonomy/resolver.py` implements a 3-level inheritance chain: agent >
  department > company. Most specific wins.
- `src/synthorg/security/trust/service.py` defines `TrustService`: per-agent trust state with
  mandatory human approval gate for ELEVATED promotion (defense-in-depth, enforced twice).

**API surface**:

| Operation | Endpoint | Notes |
|---|---|---|
| Get/set security policy | `GET/PUT /settings/security/{key}` | Key-value CRUD |
| Get autonomy config | `GET /agents/{id}/autonomy` | Per-agent override |
| Set autonomy config | `PUT /agents/{id}/autonomy` | Write access |
| List pending approvals | `GET /approvals` | Approval queue for escalated actions |
| Approve pending approval | `POST /approvals/{approval_id}/approve` | CEO/manager/board approval action |
| Reject pending approval | `POST /approvals/{approval_id}/reject` | CEO/manager/board rejection action |

**"Write Once, Enforce Everywhere" Validation**:

The policy enforcement path from configuration to runtime:

1. Admin sets a security policy via `PUT /settings/security/{key}` (handled by
   `src/synthorg/api/controllers/settings.py`)
2. `SettingsService` persists the value (SQLite); emits a settings-changed event
3. On next `AgentEngine` instantiation or reload, `SecurityConfig` is loaded from the
   settings store and passed to `SecOpsService.__init__(config=security_config, ...)`
4. `SecOpsService` builds `RuleEngine` with the resolved rule chain, incorporating any
   custom policy rules from `SecurityConfig.custom_policy_rules`
5. At every tool call, `SecOpsService.evaluate_pre_tool(context)` runs the rule engine
   against the tool invocation
6. All verdicts are logged to the audit trail

**Constraint**: `SecOpsService` takes config at construction time. Policy changes do not
propagate to running agent sessions; they apply to the next session instantiation. This is
the correct safe default (mid-session policy changes would be inconsistent), but it means
the "write once, enforce everywhere" claim applies across deployments and new sessions, not
retroactively to active long-running sessions.

**Gap G3**: Security policies are configured via `PUT /settings/{ns}/{key}` (key-value
pairs) but there is no bulk policy export/import. Operators cannot version-control their
policy as a single declarative document, export it for peer review, or import a pre-approved
policy bundle. The `SecurityConfig` Pydantic model is internally structured but has no
serialization API endpoint.

**Gap G5**: The `AuditLog` in `src/synthorg/security/audit.py` records every security
evaluation with agent, tool, verdict, and evidence, but there is no `GET /security/audit`
query endpoint. The audit trail is write-only from an API perspective; it can only be
accessed via log sinks (file/syslog/HTTP) or by directly reading the persistence layer.

---

### Primitive 3: Token Metering

**What a control plane needs**: Per-agent, per-task, per-provider cost attribution with
queryable history and enforcement at multiple boundaries.

**SynthOrg implementation**:

- `src/synthorg/budget/enforcer.py` defines `BudgetEnforcer`: three-layer enforcement:
  - Pre-flight: monthly hard stop, daily agent limit, provider quota check
  - In-flight: per-turn closure checking task limit, monthly limit, daily limit
  - Task boundary: model auto-downgrade when monthly utilization exceeds threshold
- `src/synthorg/budget/tracker.py` defines `CostTracker`: in-memory store with TTL eviction
  (168h / 7 days), asyncio.Lock for concurrent writes, category breakdown, provider usage,
  orchestration ratio.
- `src/synthorg/budget/quota.py` provides `QuotaTracker` with degradation strategies (alert,
  fallback, queue) and `SubscriptionConfig`.
- `src/synthorg/budget/coordination_metrics.py` exposes 9 coordination metrics from Kim et al.
  (2025): coordination efficiency, overhead, error amplification, message density,
  redundancy rate, Amdahl ceiling, straggler gap, token/speedup ratio, message overhead.

**API surface**:

| Operation | Endpoint | Notes |
|---|---|---|
| Budget configuration | `GET /budget/config` | Budget settings, thresholds, and enforcement config |
| Spending records | `GET /budget/records` | Paginated cost records with optional `agent_id`/`task_id` filters + daily/period summaries |
| Agent budget records | `GET /budget/agents/{agent_id}` | Per-agent total spending |
| Generate report | `POST /reports/generate` | Spending, performance, risk trends |

**Coverage**: Basic budget queries are covered. `GET /budget/config` exposes budget
configuration. `GET /budget/records` returns paginated spending records with daily and period
summaries. `GET /budget/agents/{agent_id}` provides per-agent cost totals.

**Gap G6**: `CostTracker` is in-memory with TTL eviction; it is not a durable time-series
store. Budget history granularity is limited; the tracker supports `get_agent_cost(agent_id,
start=)` and `get_total_cost(start=)` but the API does not expose multi-dimensional
queries (e.g., spending by provider X for agent Y during period Z). External cost dashboards
need this level of attribution. The persistence layer backing `GET /budget/records` needs
inspection to confirm whether it provides richer query semantics than the in-memory tracker.

**Gap G4**: The 9 coordination metrics (`budget/coordination_metrics.py`) are computed
internally and emitted as structured log events, but there is no `GET /coordination/metrics`
endpoint. Operators cannot query the coordination efficiency, overhead ratio, or Amdahl
ceiling for a completed multi-agent run. The `POST /tasks/{id}/coordinate` endpoint only
triggers coordination; it does not return or expose computed metrics. This is significant
for control-plane positioning: coordination overhead is a key indicator of whether a
multi-agent organization is operating efficiently.

---

### Primitive 4: Telemetry

**What a control plane needs**: Exportable, structured telemetry that external monitoring
systems can consume: metrics (counters, gauges, histograms), traces, and logs in standard
formats.

**SynthOrg implementation**:

- `src/synthorg/observability/setup.py` provides structlog + stdlib, idempotent `configure_logging()`,
  100+ event constant modules in `observability/events/`.
- `src/synthorg/observability/sinks.py` handles multi-sink routing: console, file (with rotation),
  syslog, HTTP batch handler.
- `src/synthorg/observability/http_handler.py` defines `HttpBatchHandler`: thread-safe queue,
  configurable batch_size (default 100) and flush_interval (default 5s), retry with backoff.
- `src/synthorg/observability/correlation.py` provides 3 correlation IDs (request_id, task_id,
  agent_id) propagated via structlog contextvars.

**API surface**:

| Operation | Endpoint | Notes |
|---|---|---|
| Analytics overview | `GET /analytics/overview` | Summary metrics (task counts, cost totals, budget status) |
| Analytics trends | `GET /analytics/trends` | Time-series cost and task-completion trends |
| Analytics forecast | `GET /analytics/forecast` | Forward-looking spend projections |
| Generate report | `POST /reports/generate` | Spending, performance, task completion |
| List log sinks | `GET /settings/observability/sinks` | Current sink configuration |
| Test sink connectivity | `POST /settings/observability/sinks/_test` | CEO/manager |
| Liveness | `GET /api/v1/healthz` | Public, process-alive check |
| Readiness | `GET /api/v1/readyz` | Public, 200 when dependencies healthy, 503 otherwise |

**Gap G1 (most significant)**: There is no Prometheus `/metrics` endpoint and no
OpenTelemetry (OTLP) exporter. The HTTP batch handler (`SinkType.HTTP`) is a log forwarder
that ships JSON log records to a configured URL; it does not implement the Prometheus
exposition format (counters, gauges, histograms with labels) or OTLP gRPC/HTTP. External
monitoring systems (Prometheus/Grafana, Datadog, Honeycomb) need one of:
- A `/metrics` endpoint scrapable by Prometheus
- An OTLP exporter sending traces and metrics to a collector

Without this, SynthOrg can ship logs to an external SIEM via HTTP sink, but cannot
participate in standard metrics pipelines or distributed tracing systems. This is the
most significant gap for enterprise control-plane positioning.

The 100+ structured event constants and correlation IDs provide the raw material for
building proper observability exports; the data model is sound, the export format is not.

---

## Gap Summary

| # | Gap | Severity | Module | Notes |
|---|-----|----------|--------|-------|
| G1 | No telemetry export (Prometheus/OTLP) | High | `observability/` | Logs only; no metrics/traces |
| G2 | No per-agent health endpoint | Medium | `hr/performance/tracker.py` | Data exists, not exposed |
| G3 | No policy-as-code export/import | Medium | `security/config.py` | Key-value CRUD only |
| G4 | No coordination metrics API | Medium | `budget/coordination_metrics.py` | Computed but not queryable |
| G5 | No audit log query API | Medium | `security/audit.py` | Write-only from API |
| G6 | Budget history granularity | Low | `budget/tracker.py` | In-memory, limited dimensions |

---

## "Write Once, Enforce Everywhere" Assessment

**Verdict**: The claim holds with one important constraint.

SynthOrg's security policies are stored in the settings layer, loaded into `SecurityConfig`,
and applied by `SecOpsService.evaluate_pre_tool()` at every tool invocation across all
agent sessions. The 3-level autonomy resolution (agent > department > company) ensures
company-wide defaults are inherited by all agents without per-agent configuration.

The constraint is session lifecycle: `SecOpsService` takes config at construction. Active
long-running sessions do not receive policy updates mid-session. This is correct behavior
(consistency within a session) but should be documented explicitly. The effective behavior
is: "write policies once, enforce across all new agent sessions."

An additional consideration: custom policy rules in `SecurityConfig.custom_policy_rules`
are Pydantic models defined in Python, not declarative rules in the settings store. This
means they require code changes, not settings API calls. This limits the "write once"
claim for custom rules to developers, not operators.

---

## Positioning Implications

### What SynthOrg Already Has

SynthOrg provides an integrated control plane that competitors typically deliver as separate
tools: inventory (HR module), policy enforcement (security module with hybrid rule engine),
token metering (3-layer budget enforcement), and observability (100+ structured events). The
integration advantage is that these components share context: budget enforcement gates
security escalation, performance tracking feeds autonomy decisions, trust levels gate
model upgrades.

This integration is genuine and hard to replicate by composing separate tools.

### What Gaps Block the Full "Control Plane" Claim

For enterprise positioning as an agentic control plane, the highest-priority gaps to close:

1. **G1 (Telemetry export)**: Required for enterprise observability requirements. A
   Prometheus `/metrics` endpoint and/or OTLP exporter would unblock integration with
   standard monitoring infrastructure. The event data model is already sound.

2. **G3 (Policy-as-code)**: Enterprise security teams want policies as versioned YAML/JSON
   files, reviewable in pull requests. A `GET /policies/export` and `POST /policies/import`
   would enable GitOps-style policy management.

3. **G4 (Coordination metrics API)**: A `GET /coordination/metrics` endpoint exposing the
   9 Kim et al. metrics would make SynthOrg uniquely differentiated: the only framework that
   exposes coordination efficiency and overhead as queryable API primitives.

### Recommended Framing

SynthOrg should be framed as an **orchestrated agent control plane**, distinct from
infrastructure control planes (Kubernetes, Terraform) that manage compute, and distinct from
swarm-style agent frameworks that lack centralized policy enforcement. The value proposition:
policy-as-code, metered coordination, and observable agent behavior, all enforced from a
single control surface.

This framing is accurate today for inventory, policy, and metering. Telemetry export is the
gap between the internal capability and the external claim.

---

## Recommendations

Priority order for closing gaps to support control-plane positioning:

1. **Prometheus `/metrics` endpoint** (G1): Add a `/metrics` route exposing key counters
   and gauges from the budget tracker, task engine, and coordination metrics. The structlog
   event stream provides all the raw data. Scope: medium (new route + metrics aggregator).

2. **`GET /agents/{id}/health` endpoint** (G2): Composite endpoint reading from
   `PerformanceTracker.get_snapshot()`, `TrustService.get_trust_state()`, and last-active
   timestamp. Scope: small (new route + service composition).

3. **Policy export/import** (G3): `GET /settings/security/export` serializing
   `SecurityConfig` to JSON, `POST /settings/security/import` validating and loading it.
   Scope: small-medium (serialization + validation).

4. **Coordination metrics API** (G4): `GET /coordination/metrics` endpoint backed by
   `CoordinationMetricsService`. Scope: medium (new route + metrics aggregation across
   completed coordination runs).

5. **Audit log query API** (G5): `GET /security/audit` with filters for agent_id,
   verdict, time range, action_type. Scope: medium (new route + query layer over audit log).
