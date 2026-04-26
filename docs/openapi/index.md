# REST API Reference

SynthOrg exposes a REST + WebSocket API built on [Litestar](https://litestar.dev/). The API is the primary integration surface for the web dashboard, the Go CLI, and any external clients that want to drive a synthetic organization programmatically.

**[Open Interactive Reference :material-open-in-new:](reference.html){ .md-button .md-button--primary }**
**[Download OpenAPI Schema :material-download:](openapi.json){ .md-button }**

---

## Base URL and Versioning

All endpoints live under a version-prefixed path:

```text
https://<your-host>/api/v1
```

The prefix is configurable via the `api_prefix` field of `ApiConfig` (default `/api/v1`). Breaking changes bump the path version; additive changes (new fields, new endpoints, relaxed constraints) ship under the existing version.

When running the server locally you also get two kinds of side paths: documentation paths (mounted by Litestar at a **fixed** prefix independent of `api_prefix`) and API paths (relative to `api_prefix`).

**Documentation paths (fixed at `/docs/*`):**

| Path | Content |
|---|---|
| `/docs/api` | Scalar UI live against your running server |
| `/docs/openapi.json` | Live OpenAPI schema for the running server |

**API paths (move with `api_prefix`, shown with the default `/api/v1`):**

| Path | Content |
|---|---|
| `/api/v1/healthz` | Liveness probe; always returns 200 while the process is alive (used by supervisors to decide whether to restart the pod) |
| `/api/v1/readyz` | Readiness probe; returns 200 when persistence + message bus are healthy, 503 otherwise (used by load-balancers to gate traffic) |
| `/api/v1/ws` | WebSocket endpoint for server-sent events (approvals, meetings, task lifecycle) |

The static snapshot on this page is produced by `scripts/export_openapi.py`, which takes the live Litestar schema and runs it through `inject_rfc9457_responses` to attach RFC 9457 error response shapes to every operation. The result is a superset of what `/docs/openapi.json` returns at runtime. The generated `reference.html` carries SEO metadata (descriptive `<title>`, `<meta description>`, `<link rel="canonical">`, and a screen-reader-only `<h1>`) so search engines can surface it as the primary "SynthOrg REST API" landing page; `openapi.json` is intentionally kept out of the sitemap because Google does not render raw JSON in search results.

---

## Authentication

SynthOrg uses **JWT session tokens** issued by the auth controller. The typical flow:

1. **First-run setup.** On a fresh install, `POST /api/v1/auth/setup` creates the initial CEO account. After setup completes, this endpoint returns a conflict error.
2. **Login.** `POST /api/v1/auth/login` with a username and password returns a `TokenResponse` carrying a signed JWT, its `expires_in` (seconds), and a `must_change_password` flag. Include the token on subsequent requests as `Authorization: Bearer <token>`. A server-side session record is created as a side effect and is retrievable via `GET /api/v1/auth/sessions`.
3. **Password change.** New users are forced through `POST /api/v1/auth/change-password` before any other endpoint accepts their token; the `require_password_changed` guard blocks everything else until the temporary password is rotated.
4. **Current identity.** `GET /api/v1/auth/me` returns the caller's `id`, `username`, `role`, and `must_change_password` flag (no session metadata).
5. **WebSocket tickets.** Browsers can't set `Authorization` headers on WebSocket connections, so `POST /api/v1/auth/ws-ticket` mints a short-lived single-use ticket. The **preferred** way to present it is as the first WebSocket message (`{"action": "auth", "ticket": "<ticket>"}`) so the ticket never lands in URLs, access logs, or browser history. A legacy `/api/v1/ws?ticket=<ticket>` query-param form is also accepted and is validated before the WebSocket upgrade.
6. **Session management.** `GET /api/v1/auth/sessions` lists the caller's active sessions by default; CEOs can pass `?scope=all` to list every user's sessions across the organization. `DELETE /api/v1/auth/sessions/{session_id}` revokes a specific session. `POST /api/v1/auth/logout` is the normal "log out of this browser" action and **attempts server-side revocation** of the JTI when a valid JWT is presented. Logout is **idempotent**: it always returns 204 with cookie-clearing headers (`Max-Age=0` session/CSRF/refresh cookies plus `Clear-Site-Data: "cookies"`), whether or not the caller is authenticated, so clients can recover from stale cookie state without a catch-22. Logout is excluded from both auth middleware and CSRF double-submit validation so recovery works from any stale-cookie state; the server-side revocation step is best-effort (a session-store failure still returns 204 and clears cookies rather than 500-ing the client). There is no bulk "revoke all" endpoint.

Passwords are hashed with Argon2id. The server performs a constant-time dummy verification on unknown usernames to prevent timing-based user enumeration.

---

## Endpoint Groups

The API is organised into resource controllers. Every controller is mounted under the `/api/v1` prefix.

### Identity and users

| Resource | Path | Purpose |
|---|---|---|
| Auth | `/auth` | Setup, login, password, sessions, WebSocket tickets |
| Users | `/users` | Human user CRUD (CEO-only), role assignment |

### Organization and agents

| Resource | Path | Purpose |
|---|---|---|
| Company | `/company` | Top-level company identity and config |
| Departments | `/departments` | Department CRUD, membership, policy overrides |
| Agents | `/agents` | Agent CRUD, hiring/firing, personality assignment |
| Agent Autonomy | `/agents/{id}/autonomy` | Per-agent autonomy level and trust policy |
| Agent Collaboration | `/agents/{id}/collaboration` | Peer collaboration rules |
| Agent Quality | `/agents/{id}/quality` | Quality score overrides (L3 human layer) |
| Activities | `/activities` | Activity timeline (lifecycle events, cost events, promotions) |
| Personalities | `/personalities` | Personality preset CRUD |

### Work and coordination

| Resource | Path | Purpose |
|---|---|---|
| Projects | `/projects` | Project CRUD, status, artifacts |
| Tasks | `/tasks` | Task CRUD, assignment, lifecycle transitions |
| Task Coordination | `/tasks/{id}/coordinate` | Multi-agent coordination actions |
| Messages | `/messages` | Inter-agent message bus access |
| Meetings | `/meetings` | Meeting scheduling, participation, minutes |
| Approvals | `/approvals` | Approval gate queue and decisions |
| Artifacts | `/artifacts` | Artifact content storage and retrieval |

### Workflows

| Resource | Path | Purpose |
|---|---|---|
| Workflows | `/workflows` | Visual workflow definition CRUD, validation, YAML export |
| Workflow Versions | `/workflows/{id}/versions` | Version history, diff, rollback |
| Workflow Executions | `/workflow-executions` | Activate, list, get, cancel executions |
| Template Packs | `/template-packs` | Additive team pack listing and live application |
| Setup | `/setup` | First-run wizard endpoints (template selection, personality seeding) |

### Operations and platform

| Resource | Path | Purpose |
|---|---|---|
| Health | `/health` | Liveness + readiness |
| Providers | `/providers` | LLM provider runtime CRUD, model auto-discovery, health |
| Budget | `/budget` | Cost tracking, spend reports, budget enforcement, risk budget |
| Analytics | `/analytics` | Aggregated metrics across agents, tasks, and providers |
| Reports | `/reports` | On-demand report generation (`POST /generate`) and period listing (`GET /periods`) |
| Memory Admin | `/admin/memory` | Fine-tuning pipeline, checkpoint management, embedder queries |
| Backups | `/admin/backups` | Backup orchestration, scheduling, retention |
| Settings | `/settings` | Runtime-editable settings (DB > env > YAML > code) |
| Ceremony Policy | `/ceremony-policy` | Project and per-department ceremony policy resolution |

Full request/response schemas for every endpoint are in the **[interactive reference](reference.html)**.

---

## Request Patterns

### Pagination

List endpoints accept `limit` and `offset` query parameters and return a `PaginatedResponse[T]` envelope:

```json
{
  "data": [...],
  "pagination": {"total": 142, "offset": 0, "limit": 50},
  "degraded_sources": [],
  "error": null,
  "error_detail": null,
  "success": true
}
```

`data` holds the page of items. `pagination` carries the offset/limit/total triple. `degraded_sources` is empty on a normal response and lists data sources that failed gracefully when the endpoint returned partial data. `error` and `error_detail` are `null` on success; `success` is derived from `error`.

### Optimistic concurrency

Runtime-editable settings emit an `ETag` header on reads and honor `If-Match` on writes. To update a setting without trampling a concurrent write, pass the previously-received ETag back via `If-Match`; a mismatch produces a `409 Conflict` with `error_code` `VERSION_CONFLICT` (4002).

Workflow definitions, workflow versions, workflow executions, and tasks use a different optimistic-concurrency mechanism: an `expected_version: int` field in the **request body** (not an HTTP header). The server rejects the update with the same `VERSION_CONFLICT` code when the stored version differs from the value supplied. Both mechanisms produce identical error shapes on conflict; only the input channel differs.

### WebSocket events

Real-time updates (approval requests, meeting state, task transitions, routing decisions) are pushed over `/api/v1/ws`. After authenticating with a ws-ticket, clients send JSON messages to subscribe or unsubscribe from named channels (with optional payload filters), and the server pushes `WsEvent` JSON payloads on subscribed channels. Event types are tagged via a `type` field on each payload.

---

## Error Format

Errors use [RFC 9457 Problem Details for HTTP APIs](https://datatracker.ietf.org/doc/html/rfc9457). The server supports two response shapes, selected via content negotiation:

**Bare `application/problem+json`** is returned when the client sends `Accept: application/problem+json`:

```json
{
  "type": "https://synthorg.io/docs/errors#validation",
  "title": "Validation Error",
  "status": 422,
  "detail": "Field 'name' is required",
  "instance": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "error_code": 2001,
  "error_category": "validation",
  "retryable": false,
  "retry_after": null
}
```

**Envelope form** (`ApiResponse[T]`) is the default, returned for `application/json` or no explicit `Accept` header:

```json
{
  "data": null,
  "error": "Field 'name' is required",
  "error_detail": {
    "detail": "Field 'name' is required",
    "error_code": 2001,
    "error_category": "validation",
    "retryable": false,
    "retry_after": null,
    "instance": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "title": "Validation Error",
    "type": "https://synthorg.io/docs/errors#validation"
  },
  "success": false
}
```

In both shapes, `instance` is the **request correlation ID** used for log tracing, not the request URL path. The `error_code` field is a 4-digit machine-readable code grouped by category, and `error_category` is the lowercase category identifier:

| Range | `error_category` | Examples (`error_code` name) |
|---|---|---|
| 1xxx | `auth` | `UNAUTHORIZED`, `FORBIDDEN`, `SESSION_REVOKED` |
| 2xxx | `validation` | `VALIDATION_ERROR`, `REQUEST_VALIDATION_ERROR` |
| 3xxx | `not_found` | `RESOURCE_NOT_FOUND`, `RECORD_NOT_FOUND`, `ROUTE_NOT_FOUND` |
| 4xxx | `conflict` | `RESOURCE_CONFLICT`, `DUPLICATE_RECORD`, `VERSION_CONFLICT` |
| 5xxx | `rate_limit` | `RATE_LIMITED` |
| 6xxx | `budget_exhausted` | `BUDGET_EXHAUSTED` |
| 7xxx | `provider_error` | Upstream LLM provider failures |
| 8xxx | `internal` | Unhandled server errors |

The `type` URI points to the category section of the [Error Reference](../errors.md), using the pattern `https://synthorg.io/docs/errors#<category>`. The full error taxonomy, including `retryable` semantics and `retry_after` behavior, lives there.

---

## Rate Limiting

The API applies three-tier rate limiting via `synthorg.api.config.RateLimitConfig` (layered on top of Litestar's built-in rate-limit middleware): an un-gated per-IP floor (default 10,000/min/IP, covers every request including those the auth middleware rejects with 401), a per-IP unauthenticated tier (default 20/min/IP, only fires when `scope["user"]` is unset), and a per-user authenticated tier (default 6,000/min/user). The floor default is sized above both user-gated caps so shared-NAT deployments do not clip legitimate traffic; a Pydantic validator on `RateLimitConfig` rejects a floor lower than either the authenticated or unauthenticated cap. All three are configurable per deployment; see `docs/security.md` for tuning, and `synthorg.api.config.RateLimitConfig` for the source-of-truth field descriptions and validator logic. Clients that exceed any tier receive `429 Too Many Requests` carrying `error_code` 5000 (`RATE_LIMITED`) and a `Retry-After` header. In the envelope form the code lives at `error_detail.error_code`.

---

## CORS

CORS is disabled by default for non-local origins. Add trusted dashboard origins via `ApiConfig.cors.allowed_origins`. Wildcard origins (`*`) cannot be combined with `allow_credentials=true`.

---

## Further Reading

- **[Interactive API Reference](reference.html)**: every endpoint, request body, and response schema
- **[OpenAPI Schema](openapi.json)**: raw schema for codegen and tooling
- **[Error Reference](../errors.md)**: full error taxonomy and codes
- **[Security](../security.md)**: authn/authz design, trust levels, audit log
- **[Architecture](../architecture/index.md)**: where the API sits in the overall system
