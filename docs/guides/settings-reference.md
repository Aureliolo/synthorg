---
title: Settings Reference
description: How SynthOrg settings resolve, the 22 runtime-editable namespaces, how to view and change settings at runtime, and which changes require a restart.
---

# Settings Reference

SynthOrg has ~100 individually-resolved settings across 22 namespaces (9 user-facing + 13 operator-only). Each setting is typed (`STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `ENUM`, `JSON`) and has a clearly-documented default. This guide covers how resolution works, which namespaces are user-facing vs operator-only, and how to edit settings at runtime.

---

## Resolution Order

Settings resolve through four layers, in priority order (first wins):

1. **Database**: values set via the REST API or dashboard persist here
2. **Environment variables** (`SYNTHORG_<NAMESPACE>_<KEY>`)
3. **YAML config file** (`synthorg-config.yaml` at boot)
4. **Code defaults** (the `SettingDefinition.default` field)

DB-backed changes take effect without restart unless the setting is marked `restart_required=True`.

## Setting Types

| Type | Example | Validation |
|------|---------|------------|
| `STRING` | `api.base_url` | Length bounds, regex |
| `INTEGER` | `api.rate_limit.auth_max_requests` | `min`/`max` bounds |
| `FLOAT` | `budget.risk_budget.per_task_risk_limit` | `gt`/`ge`/`lt`/`le` |
| `BOOLEAN` | `notifications.min_severity.enabled` | true/false |
| `ENUM` | `observability.root_log_level` | Validated against `enum_values` |
| `JSON` | `providers.configs` | Pydantic schema |

Values marked `sensitive=True` (API keys, webhook URLs, passwords) are Fernet-encrypted at rest and returned from GET responses as `"***"` placeholders.

## Namespaces

### User-facing (visible in the dashboard)

| Namespace | What it configures |
|-----------|---------------------|
| `api` | Rate limits, CORS, request timeouts, auth cookie settings |
| `company` | Company name, autonomy level, monthly budget, communication pattern |
| `providers` | LLM provider CRUD, routing strategy, SSRF discovery allowlist |
| `memory` | Memory backend, retention, embedding model, consolidation policy |
| `budget` | Monthly budget, currency, alerts, auto-downgrade, risk budget |
| `security` | Autonomy levels, approval policies, output scanner, trust strategy, policy engine |
| `coordination` | Coordination metrics, error taxonomy, orchestration ratio alerts |
| `observability` | Log level, correlation tracking, sink overrides, custom sinks |
| `backup` | Enabled, schedule, compression, retention count/age |

### Operator-only (operator-tunable, hidden from the basic UI)

These surface previously-hardcoded timeouts, batch sizes, and resource limits. All default to the `ADVANCED` level.

| Namespace | What it configures |
|-----------|---------------------|
| `engine` | Prompt profiles, stagnation detection, context compaction, evolution, crash recovery |
| `communication` | Message bus configuration, delegation policies, meeting protocol timeouts |
| `a2a` | A2A gateway auth, allowlist, agent card verification, webhook security |
| `integrations` | Secret backend, OAuth manager, health prober interval, webhook dedup window |
| `meta` | Self-improvement signal aggregation, rollout strategies, proposer model |
| `notifications` | Sink registry, dispatcher timeout, severity threshold |
| `tools` | Sandbox backends, tool access levels, progressive disclosure thresholds |
| `settings` | Dispatcher polling interval, change-notification channel |
| `client` | Human-response timeout, scored-feedback passing score / strictness multiplier / floor for synthesised AIClients |
| `hr` | Training-pipeline kill switch, evaluation metric toggles (quality, cost, latency, task count) |
| `simulations` | Per-run timeouts for synthetic-client task and code-review simulations |
| `telemetry` | Anonymous product telemetry opt-in (off by default; token embedded at build) |
| `workers` | Uvicorn worker count, distributed dispatcher publish retry budget and backoff |

### Security headers and error documentation

The `api` namespace also carries operator-tunable settings that govern the response surface of `/docs/` and RFC 9457 error payloads, and the `notifications` namespace has a Slack default URL fallback:

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `api.csp_docs_external_origins` | JSON list | `["https://cdn.jsdelivr.net", "https://fonts.scalar.com", "https://proxy.scalar.com"]` | Trusted external origins used to build the relaxed Content-Security-Policy on `/docs/` paths. Override with internally-mirrored hosts when the backend is not allowed to reach the public Scalar CDN. Each origin must match `^https?://[\w.\-:/]+$`; a malformed entry rejects the bridge config and the runtime falls back to defaults with a `WARNING` log. |
| `api.error_docs_base_url` | STRING | `https://synthorg.io/docs/errors` | Base URL appended with `#<category>` for the RFC 9457 `type` field on every error response. HTTPS-only (`^https://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:/[^\s?#]*)?$`); userinfo, query, and fragment components are rejected at runtime. |
| `notifications.slack_default_webhook_url` | STRING (sensitive) | `""` | Optional fallback Slack incoming webhook applied when a Slack sink is configured without its own `webhook_url`. Empty default keeps every sink explicit; setting a value lets operators centralise the URL. Encrypted at rest. |

All three are `restart_required=True`: the CSP and error-docs URL are baked into module-level state during startup; the Slack default is read at sink construction and is not hot-reloaded.

## REST API

All namespaces expose the same endpoint pattern:

```bash
# List all settings in a namespace with current values
curl http://localhost:3001/api/v1/settings/api \
  -H "Cookie: session=${TOKEN}"

# Get a single setting's schema (type, default, bounds, description)
curl http://localhost:3001/api/v1/settings/api/rate_limit.auth_max_requests/schema \
  -H "Cookie: session=${TOKEN}"

# Update a single setting
curl -X PUT http://localhost:3001/api/v1/settings/api/rate_limit.auth_max_requests \
  -H "Content-Type: application/json" \
  -H "Cookie: session=${TOKEN}" \
  -d '{"value": 12000}'

# Reset a setting to its default
curl -X DELETE http://localhost:3001/api/v1/settings/api/rate_limit.auth_max_requests \
  -H "Cookie: session=${TOKEN}"
```

Security policy settings can be exported and re-imported as a bundle:

```bash
# Export all registered security settings
curl http://localhost:3001/api/v1/settings/security/export \
  -H "Cookie: session=${TOKEN}" > security-policy.json

# Import into another deployment
curl -X POST http://localhost:3001/api/v1/settings/security/import \
  -H "Content-Type: application/json" \
  -H "Cookie: session=${TOKEN}" \
  -d @security-policy.json
```

## Restart-Required Settings

Some settings are bootstrap-only and cannot be hot-reloaded safely. They are marked with `restart_required=True` in the schema. Common examples:

- `api.rate_limit.floor_max_requests` / `unauth_max_requests` / `auth_max_requests` (the three-tier rate limiter builds at startup)
- `api.per_op_rate_limit.backend` / `api.per_op_concurrency.backend` (the per-op stores are constructed once at startup; enabled / overrides ARE runtime-editable)
- `api.cors.allowed_origins` (Litestar CORS plugin registers at construction)
- `backup.path` (backup scheduler's output directory)
- `observability.ws_ticket_max_pending_per_user` (ticket store is constructed once)

Changing a restart-required setting writes the new value to the database but the running process continues using the old value. Restart the backend to pick up the change.

## Hot-reloaded Settings

The `SettingsChangeDispatcher` polls the `#settings` message bus channel and routes change events to registered `SettingsSubscriber` implementations. Concrete subscribers today:

- `ProviderSettingsSubscriber`: rebuilds `ModelRouter` on `routing_strategy` change via `AppState.swap_model_router()`
- `MemorySettingsSubscriber`: advisory logging for non-restart memory settings
- `BackupSettingsSubscriber`: toggles `BackupScheduler` on `enabled` change, reschedules on `schedule_hours` change

Settings resolved via `ConfigResolver` bridge configs (e.g. `get_communication_bridge_config()`) are re-fetched at the top of each polling iteration in their consumers, so operator changes take effect within one poll cycle without restart.

## Per-Operation Rate Limiting

Two layered rate-limit subsystems sit on top of the global three-tier
limiter (``api.rate_limit.*``). Both are runtime-editable via settings.

### Sliding-window guard (``api.per_op_rate_limit.*``)

| Setting | Type | Default | Runtime-editable | Purpose |
|---------|------|---------|------------------|---------|
| `api.per_op_rate_limit.enabled` | BOOLEAN | `true` | yes | Master switch; when `false` every `per_op_rate_limit` guard becomes a no-op. |
| `api.per_op_rate_limit.backend` | ENUM | `memory` | no (restart) | Sliding-window store backend. `memory` is the only implementation today; `redis` reserved for cross-worker fairness. |
| `api.per_op_rate_limit.overrides` | JSON | `{}` | yes | Per-operation overrides keyed by operation name. Shape: `{"<op>": [max_requests, window_seconds]}`. Setting either component to `0` disables the guard for that operation; negative values are rejected. |

Example override to tighten ``memory.fine_tune`` to two starts per day.
The ``SettingsController`` routes by ``(namespace, key)`` where ``key``
is the registry key (underscores), not the yaml_path (dots):

```bash
curl -X PUT http://localhost:3001/api/v1/settings/api/per_op_rate_limit_overrides \
  -H "Content-Type: application/json" \
  -H "Cookie: session=${TOKEN}" \
  -d '{"value": "{\"memory.fine_tune\": [2, 86400]}"}'
```

### Inflight concurrency guard (``api.per_op_concurrency.*``)

| Setting | Type | Default | Runtime-editable | Purpose |
|---------|------|---------|------------------|---------|
| `api.per_op_concurrency.enabled` | BOOLEAN | `true` | yes | Master switch for the `PerOpConcurrencyMiddleware`. |
| `api.per_op_concurrency.backend` | ENUM | `memory` | no (restart) | Inflight-counter store backend. `memory` today; `redis` reserved. |
| `api.per_op_concurrency.overrides` | JSON | `{}` | yes | Per-operation overrides keyed by operation name. Shape: `{"<op>": <max_inflight>}`. `0` disables; negative values are rejected. |

The six endpoints that declare an inflight cap by default:
``memory.fine_tune`` (shared with ``memory.fine_tune_resume``),
``memory.checkpoint_deploy``, ``memory.checkpoint_rollback``,
``providers.pull_model``, ``providers.discover_models``.

## Common Configuration Patterns

### Switch LLM providers

Add or update a provider via `/api/v1/providers`, set `routing.strategy` via `/api/v1/settings/providers/routing_strategy` to `smart` (or the strategy of your choice). The model router rebuilds immediately.

### Enable agent sandbox

Set `tools.sandboxing.default_backend` to `docker` in the `tools` namespace. Pull the sandbox image once via `synthorg start --sandbox true`. The backend spawns ephemeral sandbox containers per tool invocation.

### Adjust ceremony strategy

Edit `coordination.ceremony.strategy` in the `coordination` namespace. See [Ceremony Scheduling](../design/ceremony-scheduling.md) for the available strategies.

### Swap log sinks

Use `observability.custom_sinks` (JSON-typed) to add HTTP / syslog / OTLP shipping. See [Centralised Logging](centralized-logging.md) for examples.

---

## See Also

- [Company Configuration](company-config.md): YAML bootstrap config reference
- [Security & Trust Policies](security.md): autonomy, approvals, trust
- [Centralised Logging](centralized-logging.md): log sink configuration
- [Design: Observability](../design/observability.md): architecture and event taxonomy
