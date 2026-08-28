---
title: Human Interaction Layer
description: REST/WebSocket API surface, rate limiting, RFC 9457 error responses, Web UI features, and human roles.
---

# Human Interaction Layer

This guide covers how humans and external systems interact with SynthOrg: the API-first architecture, the endpoint surface, per-operation rate limiting, RFC 9457 structured error responses, Web UI features, and the role-based access model.

---

## API-First Architecture

The REST/WebSocket API is the **primary interface** for all consumers. The Web UI and CLI
are thin clients that call the API; they contain no business logic.

```d2
Engine: "SynthOrg Engine\n(Core Logic, Agent Orchestration, Tasks)"
API: "REST/WS API\n(Litestar)"
WebUI: "Web UI\n(React)"
CLI: "CLI Tool\n(Go)"

Engine -> API
API -> WebUI
API -> CLI
```

!!! info "CLI Tool"

    Cross-platform Go binary (`cli/`) for Docker lifecycle management. Commands: `init`
    (interactive setup wizard), `start`, `stop`, `status`, `logs`, `update` (CLI self-update
    from GitHub Releases with automatic re-exec, channel-aware (stable/dev), compose
    template refresh with diff approval, container image update with version matching), `doctor`
    (diagnostics + bug report URL), `uninstall`, `version`, `config`, `completion-install`,
    `backup` (create/list/restore via backend API), `wipe` (factory-reset with interactive backup and restart prompts),
    `cleanup` (remove old container images to free disk space).
    Built with Cobra + charm.land/huh/v2 + charm.land/lipgloss/v2. Distributed via GoReleaser + install scripts
    (`curl | sh` for Linux/macOS, `irm | iex` for Windows).
    Global output modes: `--quiet` (errors only), `--verbose/-v` (verbose/trace), `--plain`
    (ASCII-only), `--json` (machine-readable), `--no-color`, `--yes` (non-interactive).
    Typed exit codes: 0 (success), 1 (runtime), 2 (usage), 3 (unhealthy), 4 (unreachable),
    10 (update available). Key flags have corresponding `SYNTHORG_*` or standard env vars.

## API Surface

| Endpoint | Purpose |
|----------|---------|
| `/api/v1/healthz` | Liveness probe; always 200 while the process is alive (used by supervisors to decide whether to restart the pod) |
| `/api/v1/readyz` | Readiness probe; 200 when every configured dependency (persistence, message bus, providers) is healthy, 503 otherwise (used by load-balancers to gate traffic). Body is topology- and version-free (binary outcome + uptime) |
| `GET /api/v1/health` | Authenticated per-component health breakdown (persistence / message bus / providers / telemetry) for the dashboard; requires a read-access role |
| `/api/v1/metrics` | Prometheus metrics scrape endpoint (unauthenticated). Core families refreshed per-scrape: `synthorg_app_info` (Info, version), `synthorg_active_agents_total` (Gauge with status, trust_level labels), `synthorg_tasks_total` (Gauge with a status label only; a per-agent label was deliberately dropped as an unbounded-cardinality source, so per-agent task and cost breakdowns live in the REST task/cost APIs instead), `synthorg_cost_total` (Gauge, aggregate only), `synthorg_budget_used_percent` / `synthorg_budget_daily_used_percent` (Gauge, each paired with a `synthorg_budget_*_spend_measurability` state gauge that flags whether the percentage is a real measurement, understated, or unmeasurable). Push-updated on their own trigger rather than per-scrape: `synthorg_coordination_efficiency` / `synthorg_coordination_overhead_percent` (Gauge, after each multi-agent execution), `synthorg_security_evaluations_total` (Counter with a verdict label), and several dozen further counters and gauges covering approval decisions, provider calls, auth, the audit chain, WebSocket connections, and the Postgres connection pool. This list is representative, not exhaustive; scrape the endpoint for the current set. |
| `/api/v1/auth` | Authentication: setup, login (HttpOnly cookie sessions, CSRF double-submit), password change (rotates session cookie), ws-ticket, session management (list/revoke, concurrent session limits), logout, account lockout, refresh token rotation (three-tier rate limiting; see `docs/security.md`) |
| `/api/v1/auth/api-keys` | API-key management: issue (plaintext returned once), list (hash never exposed), revoke (owner-or-CEO, 404 for non-owner). Issuance is role-ceiling capped; issue/revoke emit signed audit-chain events |
| `/api/v1/company` | CRUD company config |
| `/api/v1/agents` | List, hire, fire, modify agents |
| `GET /api/v1/agents/{name}/health` | Per-agent composite health (performance, trust, lifecycle status) |
| `GET /api/v1/agents/{name}/performance` | Agent performance metrics summary |
| `GET /api/v1/agents/{name}/activity` | Paginated agent activity timeline (lifecycle, task, cost, tool, delegation events); `degraded_sources` included in `PaginatedResponse` contract |
| `GET /api/v1/agents/{name}/history` | Agent career history events |
| `GET /api/v1/activities` | Org-wide activity feed |
| `/api/v1/departments` | Department management |
| `/api/v1/projects` | Project listing, creation, and retrieval |
| `/api/v1/tasks` | Task management |
| `POST /api/v1/tasks/{task_id}/coordinate` | Trigger multi-agent coordination |
| `/api/v1/messages` | Communication log |
| `/api/v1/artifacts` | Artifact listing, creation, retrieval, deletion with binary content upload/download |
| `/api/v1/budget` | Spending, limits, projections |
| `/api/v1/approvals` | Pending human approvals queue: agent escalations parked mid-execution and review-gate decisions (autonomy, hiring, promotion, ...) in one inbox, filterable by `source` |
| `POST /api/v1/meta/chat/turn` | The unified "talk to your org" turn: one endpoint classifies the message to an intent (`explain` / `propose` / `act` / `group_convene` / `charter`) and dispatches to the matching capability, returning the classified `intent`, `intent_reason`, the one payload that fits, and any specialist `chime_ins` (multi-voice). Uncertainty degrades to `explain`, never `act`/`charter`; each intent keeps its own `chief_of_staff.*_enabled` gate (a closed gate 503s), `act` stays fail-closed + buffered. Rate-limited `meta.chat.turn` (5/60s/user); optional `Idempotency-Key` for idempotent replay |
| `POST /api/v1/meta/chat/turn/stream` | Streaming sibling of the unified turn: an `explain` turn streams over SSE (`delta` frames token-by-token, then a `complete` frame carrying the full result, then a `chime` frame per specialist). Every other intent emits a single `deferred` frame carrying the classified intent and targets, and the client re-issues it against the buffered `POST /api/v1/meta/chat/turn`, so a side-effecting turn never runs on the stream |
| `GET /api/v1/meta/chat/conversations`, `GET /api/v1/meta/chat/conversations/{id}` | Resume: owner-scoped, cursor-paginated conversation list + a conversation's turns (foreign/unknown id → 404). Backs reloading a prior transcript |
| `GET /api/v1/agents/active` | Active-agent roster (stable UUIDs, names, roles); backs group-convene participant resolution and multi-voice attribution |
| `/api/v1/analytics` | `GET /overview` (metrics summary), `GET /trends?period=7d\|30d\|90d` (bucketed time-series), `GET /forecast?horizon_days=1..90` (budget projection) |
| `POST /api/v1/reports/generate`, `GET /api/v1/reports/periods` | On-demand report generation (spending, performance, task completion, risk trends) |
| `/api/v1/settings` | Runtime-editable configuration (<!--RS:settings_namespaces-->34<!--/RS--> namespaces), schema discovery |
| `GET /api/v1/settings/security/export`, `POST /api/v1/settings/security/import` | Security policy export/import |
| `GET /api/v1/security/audit` | Audit log query with filters |
| `GET /api/v1/coordination/metrics` | Coordination metrics query (the Kim et al. multi-agent-vs-single-agent coordination-efficiency metric suite) |
| `/api/v1/providers/*` | Provider CRUD, presets, model discovery, discovery SSRF allowlist, local model management (pull with SSE progress, delete, per-model config) |
| `/api/v1/providers/model-refresh/*` | Upgrade recommendations (`GET /recommendations`, approve/reject), manual `POST /refresh` (CEO/manager), `GET /status` |
| `GET /api/v1/projects/{project_id}/docs/{slug}/receipt` | Deliverable receipt with provenance; `/receipt/validate` re-checks integrity |
| `/api/v1/setup/*` | First-run setup wizard |
| `/api/v1/users` | CEO-only user CRUD |
| `/api/v1/admin/backups` | Manual backup, list, detail, delete |
| `/api/v1/ws` | WebSocket for real-time updates. First-message auth preferred: connect without query params, then send `{"action":"auth","ticket":"<ticket>"}`. Query-param `?ticket=` is a legacy fallback. |
| `POST /api/v1/auth/ws-ticket` | Exchange JWT for one-time WebSocket connection ticket |

## Per-Operation Rate Limiting

In addition to the global three-tier limiter applied in `api/app.py`
(10,000 req/min/IP floor, 20 req/min unauth by IP, 6,000 req/min auth
by user ID; see `docs/security.md` for the exact behaviour), a
pluggable sliding-window limiter throttles individual expensive or
abuse-prone operations. Guards are declared at the route level via
`per_op_rate_limit("<op>", max_requests=N, window_seconds=W, key=...)`
from `synthorg/api/rate_limits/guard.py`.

- **Backend**: `SlidingWindowStore` protocol with an in-memory
  implementation. Configuration lives in `PerOpRateLimitConfig` under
  `api.per_op_rate_limit` with an `overrides: {op -> (max_requests,
  window_seconds)}` map that takes effect without restart. Setting
  either component to `0` disables the operation; negative values are
  rejected at startup with a logged error for diagnosability.
- **Keying**: `user`, `ip`, or `user_or_ip` (default).  The ``ip``
  source is the proxy-normalised ``trusted_client_ip`` populated on
  the ASGI scope; the raw ``X-Forwarded-For`` header is NOT trusted.
- **Denials** raise `PerOperationRateLimitError` (`error_code=5001`,
  `error_category=rate_limit`, `retryable=True`).  Because
  `PerOperationRateLimitError` subclasses `ApiError`, responses are
  shaped by `handle_api_error()`, which emits a `Retry-After` header
  whenever the exception supplies `retry_after`; the header value
  agrees with the envelope's `retry_after` field. Missing wiring is
  treated as a deployment error and fails closed with a 429 rather
  than silently skipping.
- **Throttled endpoints** (initial set):
  - `POST /api/v1/auth/ws-ticket` (20/60s by user)
  - `PUT /api/v1/artifacts/{id}/content` (10/60s by user)
  - `POST /api/v1/admin/backups/restore` (3/3600s by user)
  - `POST /api/v1/setup/complete` (5/3600s by user_or_ip)
  - `POST /api/v1/simulations` (30/3600s by user)
  - `PUT`/`DELETE /api/v1/settings/{namespace}/{key}` (60/60s by user)
  - `POST /api/v1/a2a/*` (120/60s by user_or_ip)
  - `GET /api/v1/oauth/callback` (30/60s by ip)
  - `POST /api/v1/approvals/{id}/approve` and `/reject` (100/60s each, by user)

## Domain Error Handler Registration

A single `handle_domain_error` handler (registered in
`api/exception_handlers.EXCEPTION_HANDLERS`) maps eight domain error
base classes to RFC 9457 responses: `EngineError`,
`BudgetExhaustedError`, `MixedCurrencyAggregationError`, `ProviderError`,
`OntologyError`, `CommunicationError`, `IntegrationError`, `ToolError`.  Each base
carries `status_code` / `error_code` / `error_category` / `retryable`
/ `default_message` as ClassVar metadata so MRO dispatch picks up
subclass overrides automatically.  5xx responses return the class-level
`default_message`; 4xx pass the controller-authored message through
(user-safe).  A single `is_retryable=True` on a subclass is sufficient
to surface `retryable: true` in the envelope even when the base class
opts out; the handler prefers the instance flag when set.

## Error Response Format (RFC 9457)

All error responses follow [RFC 9457 (Problem Details for HTTP APIs)](https://www.rfc-editor.org/rfc/rfc9457).
The API supports two response formats via content negotiation:

- **Default (`application/json`)**: `ApiResponse` envelope with `error_detail` object
- **RFC 9457 bare (`application/problem+json`)**: Flat `ProblemDetail` body with `Content-Type: application/problem+json`

Clients request bare RFC 9457 responses by sending `Accept: application/problem+json`.

### ErrorDetail Fields (Envelope Format)

The `error_detail` object in the envelope contains:

| Field | Type | Description |
|-------|------|-------------|
| `detail` | `str` | Human-readable occurrence-specific explanation |
| `error_code` | `int` | Machine-readable 4-digit code (category-grouped: 1xxx=auth, 2xxx=validation, 3xxx=not_found, 4xxx=conflict, 5xxx=rate_limit, 6xxx=budget_exhausted, 7xxx=provider_error, 8xxx=internal) |
| `error_category` | `str` | High-level category: `auth`, `validation`, `not_found`, `conflict`, `rate_limit`, `budget_exhausted`, `provider_error`, `internal` |
| `retryable` | `bool` | Whether the client should retry the request |
| `retry_after` | `int \| null` | Seconds to wait before retrying (null when not applicable) |
| `instance` | `str` | Request correlation ID for log tracing |
| `title` | `str` | Static per-category title (e.g., "Authentication Error") |
| `type` | `str` | Documentation URI for the error category (e.g., `https://synthorg.io/docs/errors#auth`) |

### ProblemDetail Fields (RFC 9457 Bare Format)

When `Accept: application/problem+json`, the response body contains:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | Documentation URI for the error category |
| `title` | `str` | Static per-category title |
| `status` | `int` | HTTP status code |
| `detail` | `str` | Human-readable occurrence-specific explanation |
| `instance` | `str` | Request correlation ID for log tracing |
| `error_code` | `int` | Machine-readable 4-digit error code |
| `error_category` | `str` | High-level error category |
| `retryable` | `bool` | Whether the client should retry |
| `retry_after` | `int \| null` | Seconds to wait before retrying |

Agent consumers can use `retryable` and `retry_after` for autonomous retry logic,
`error_code` / `error_category` for programmatic error handling without parsing
message strings, and `type` URIs for documentation lookup.

See the [Error Reference](../errors.md) for the full error taxonomy, code list,
and retry guidance.

## Web UI Features

!!! note "Status"

    The Web UI is built as a React 19 + shadcn/ui + Tailwind CSS dashboard. The API
    remains fully self-sufficient for all operations; the dashboard is a thin client.

For the full-page list, navigation hierarchy, URL routing map, and WebSocket channel subscriptions, see [Page Structure & IA](../design/page-structure.md).

**Primary navigation** (sidebar, always visible):

- **Dashboard** (`/`): org overview, department health indicators, recent activity widget, budget snapshot, active task summary, agent status counts, approval badge count
- **Org Chart** (`/org`): living org visualization with hierarchy and communication graph views, real-time agent status, drag-drop agent reassignment. Merged with former Company page; "Edit Organisation" mode (`/org/edit`) provides form-based company config CRUD with sub-tabs (General, Agents, Departments)
- **Task Board** (`/tasks`): Kanban (default) and list view toggle. Task detail includes "Coordinate" action for multi-agent coordination
- **Budget** (`/budget`): P&L management dashboard with current spend vs budget, per-agent/department breakdowns, trend lines, forecast projections (`/budget/forecast`)
- **Approvals** (`/approvals`): Pending decisions queue with risk-level badges, approve/reject with comment, history view

**Secondary navigation** (sidebar, collapsible "Workspace" section):

- **Agents** (`/agents`): Agent profile cards/table. Click navigates to Agent Detail page (`/agents/{agentName}`)
- **Messages** (`/messages`): Channel-filtered agent-to-agent communication feed
- **Providers** (`/providers`): LLM provider CRUD, connection test, preset-based creation, model auto-discovery, model pull dialog with SSE streaming progress, model deletion, per-model launch parameter configuration drawer
- **Settings** (`/settings`): Configuration for UI-visible namespaces (api, memory, budget, security, coordination, observability, backup). Namespace tab bar navigation with single-column layout, basic/advanced mode, GUI/Code edit toggle. Observability sinks sub-page (`/settings/observability/sinks`) for log sink management.

Settings details:

- *DB-backed persistence*: <!--RS:settings_namespaces-->35<!--/RS--> namespaces total. User-facing: api, company, providers, memory, budget, security, coordination, observability, backup. Bridged / operator-only: the rest, from `engine` and `communication` through to `telemetry` and `output_style`; [Settings Reference](settings-reference.md) carries the current split. Setting types: `STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `ENUM`, `JSON`. 3-tier resolution: DB > env > code defaults (Cat-1); env > defaults for `compose_set` (Cat-2); bootstrap secrets are pure env at the boot site (Cat-3). Fernet encryption for `sensitive` values.
- *`ConfigResolver`*: Typed scalar accessors assemble full Pydantic config models from individually resolved settings (parallel via `asyncio.TaskGroup`). Structural data accessors (`get_agents`, `get_departments`, `get_provider_configs`) resolve JSON-typed settings with Pydantic schema validation and graceful fallback.
- *Live application*: `SettingsChangeDispatcher` polls the `#settings` bus channel and routes change notifications to registered `SettingsSubscriber` implementations. Every published change is actionable: a `compose_set` setting is rejected on write, so it never publishes one.

## Human Roles

| Role | Access | Description |
|------|--------|-------------|
| **Board Member** | Read-only + approve/reject | Strategic oversight; can view all resources and decide on pending approvals, but cannot create or modify resources |
| **CEO** | Full authority, user management | Human IS the CEO, agents are the team. Sole authority to create, modify, and delete user accounts |
| **Manager** | Department-level authority | Manages one team/department directly |
| **Observer** | Read-only | Watch the company operate, no intervention |
| **Pair Programmer** | Direct collaboration with one agent | Work alongside a specific agent in real-time |
| **System** | Write (backup/wipe only) | Internal CLI-to-backend identity. Cannot log in, be deleted, or be modified. Scoped to backup/restore/wipe endpoints only. Bootstrapped at startup. |

---

## See Also

- [Design Overview](../design/index.md)
- [Security & Approval](../design/security.md): approval gates, autonomy levels
- [Error Reference](../errors.md)
