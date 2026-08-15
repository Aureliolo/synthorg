---
title: Settings Reference
description: How SynthOrg settings resolve, the runtime-editable namespaces, how to view and change settings at runtime, and which settings the deployment fixes instead.
---

# Settings Reference

SynthOrg has over 300 individually-resolved settings across <!--RS:settings_namespaces-->36<!--/RS--> namespaces, split between user-facing namespaces (visible in the dashboard) and operator-only namespaces (operator-tunable, hidden from the basic UI). Each setting is typed (`STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `ENUM`, `JSON`) and has a clearly-documented default. This guide covers how resolution works, which namespaces are user-facing vs operator-only, and how to edit settings at runtime. <!-- lint-allow: doc-numeric-macros -- approximate floor; total settings count is not a tracked runtime stat -->

---

## Resolution Order

Settings resolve through three sources, in priority order (first wins):

1. **Database**: values set via the REST API or dashboard persist here
2. **Environment variables** (`SYNTHORG_<NAMESPACE>_<KEY>`)
3. **Code defaults** (the `SettingDefinition.default` field)

YAML (`synthorg-config.yaml`) is a company-template ingestion format, not a
precedence tier: `synthorg init` reads it once to seed the database, and its
values are thereafter resolved through the chain above. See
[Configuration Precedence](../reference/configuration-precedence.md) for the
full model.

A DB-backed change takes effect without a restart: a setting its consumer resolves per operation is live on the next call, and one applied by a subscriber lands on the next dispatch poll. The exception is a setting marked `compose_set=True`, which the deployment fixed when the process started; the dashboard shows it read-only and a write is rejected rather than stored.

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
| `company` | Company name, autonomy level, monthly budget, currency, model-spend profile, communication pattern |
| `providers` | LLM provider CRUD, routing strategy, SSRF discovery allowlist |
| `memory` | Memory backend, retention, embedding model, consolidation policy |
| `budget` | Monthly budget, currency, alerts, run and token ceilings, risk budget, quota poller |
| `security` | Autonomy levels, approval policies, output scanner, policy engine |
| `coordination` | Coordination metrics, error taxonomy, orchestration ratio alerts |
| `observability` | Log level, correlation tracking, sink overrides, custom sinks |
| `appearance` | Dashboard theme axes (colour palette, density, typography, animation, sidebar mode) |
| `dashboard` | Misc dashboard UI preferences (sidebar collapsed, recent commands, advanced-mode toggles, dismissals) |
| `org_chart` | Org-chart view preferences (particle flow, badges, status dots, minimap, collapsed departments) |
| `backup` | Enabled, schedule, compression, retention count/age |
| `cockpit` | Flight-recorder run replay, stuck/runaway thresholds, snapshot cadence, steering proposer and active-directive limits |

### Operator-only (operator-tunable, hidden from the basic UI)

These surface previously-hardcoded timeouts, batch sizes, and resource limits. All default to the `ADVANCED` level.

| Namespace | What it configures |
|-----------|---------------------|
| `engine` | Prompt profiles, stagnation detection, context compaction, evolution, crash recovery, health monitoring |
| `communication` | Message bus configuration, delegation policies, meeting protocol timeouts |
| `a2a` | A2A gateway auth, allowlist, agent card verification, webhook security |
| `integrations` | Secret backend, OAuth manager, health prober interval, webhook dedup window |
| `meta` | Self-improvement signal aggregation, rollout strategies, proposer model |
| `notifications` | Sink registry, dispatcher timeout, severity threshold |
| `tools` | Sandbox backends, tool access levels, progressive disclosure thresholds |
| `settings` | Dispatcher polling interval, change-notification channel |
| `client` | Human-response timeout, scored-feedback passing score / strictness multiplier / floor for synthesised AIClients |
| `hr` | Training-pipeline kill switch, evaluation metric toggles (quality, cost, latency, task count) |
| `simulations` | Client-intake benchmark door toggle (`client_intake_enabled`, off by default) and per-run timeouts for synthetic-client task and code-review simulations |
| `telemetry` | Anonymous product telemetry opt-in (off by default; token embedded at build) |
| `workers` | Uvicorn worker count; distributed dispatcher publish retry budget and backoff |
| `research` | Research-mode provider/model and pipeline strategies (query planning, credibility triage, deduplication, synthesis) |
| `charter` | Deep CEO-interview charter pacing (model, turns, temperature, token budget) and default currency |
| `external_api` | Governed external API access: provider, response-size cap, timeout, and per-minute rate limit |
| `self_improvement` | Self-modifying meta-loop: master and per-strategy toggles, toolsmith gate, per-call models, and structural tuning (schedule, rollout, regression, guards); every switch defaults off |
| `chief_of_staff` | Unified-chat + Chief-of-Staff capability flags (turn-router, multi-voice, propose, concern-routing, group-chat, learning, alerts, narrative, invite, direct-MCP) and per-feature models |
| `knowledge` | Knowledge substrate (document ingestion + retrieval) enable and optional generative-RAG synthesis (model, strategy, per-answer chunk budget) |
| `design` | Image-generation master flag and the image model the design `image_generator` tool routes through |
| `strategy` | Anti-trendslop meeting policy: what a meeting does when it converges too fast, the threshold that counts as too fast, and who takes part in the premortem |
| `demo` | Demo-mode showcase content (e.g. greeting copy) |

### Security headers and error documentation

The `api` namespace also carries operator-tunable settings that govern the response surface of `/docs/` and RFC 9457 error payloads:

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `api.csp_docs_external_origins` | JSON list | `["https://cdn.jsdelivr.net", "https://fonts.scalar.com", "https://proxy.scalar.com"]` | Trusted external origins used to build the relaxed Content-Security-Policy on `/docs/` paths. Override with internally-mirrored hosts when the backend is not allowed to reach the public Scalar CDN. Each origin must match `^https?://[\w.\-:/]+$`; a malformed entry rejects the bridge config and the runtime falls back to defaults with a `WARNING` log. |
| `api.error_docs_base_url` | STRING | `https://synthorg.io/docs/errors` | Base URL appended with `#<category>` for the RFC 9457 `type` field on every error response. HTTPS-only (`^https://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:/[^\s?#]*)?$`); userinfo, query, and fragment components are rejected at runtime. |

Both apply without a restart: `ApiSecurityHeadersSettingsSubscriber` re-resolves the pair on the next dispatch poll and pushes it onto the module-level state the header builder reads.

The Slack notification sink binds a `SLACK` connection (its `notifications.slack_timeout_seconds` bridge setting is hot-reloadable), and the forge / chat agent tools are configured under the `tools` namespace (`forge_tools_enabled` / `forge_tools_connection`, `chat_tools_enabled` / `chat_tools_connection`), applied on the next runtime rebuild.

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
  -d '{"value": 1200}'

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

## Compose-Set Settings

A few settings are decided when the container is created and cannot be changed by the process running inside it. They carry `compose_set=True`, appear read-only in the dashboard under **Advanced · set by the deployment**, and reject a write instead of storing a value nothing will read. Common examples:

- `api.server_host` / `api.server_port` / `api.ssl_certfile` / `api.ssl_keyfile` / `api.ssl_ca_certs` (the listening socket uvicorn already opened)
- `api.api_prefix` (every route path, and the dashboard is built against it)
- `api.cors_allowed_origins`, `api.auth_exclude_paths`, `api.rate_limit_exclude_paths` (Litestar caches the CORS config and applies middleware exclusions at mount time)
- `tools.sandbox_image` / `sidecar_image` (the CLI pulled and signature-verified these; the container was created against the resolved digest)
- `observability.tsa_endpoint_freetsa` / `tsa_endpoint_digicert` / `tsa_endpoint_sectigo` (the timestamp trust anchor is resolved before the settings backend exists, and swapping the authority mid audit-chain is security-sensitive)

To change one, edit it where the process is launched (both backend compose files, or the worker launch command for a worker-only key) and restart that process. `scripts/check_setting_compose_backed.py` fails any `compose_set` key the shipped compose template does not actually set, so the flag cannot be used to mean "not wired up".

## Hot-reloaded Settings

The `SettingsChangeDispatcher` polls the `#settings` message bus channel and routes change events to registered `SettingsSubscriber` implementations. Concrete subscribers today:

- `ProviderSettingsSubscriber`: rebuilds the provider registry on `retry_max_attempts` change and triggers a runtime-services rebuild so the running engine adopts the new cap
- `BackupSettingsSubscriber`: toggles `BackupScheduler` on `enabled` change, reschedules on `schedule_hours` change, re-points the backup path on `path` change, and re-applies the `compression` / `on_shutdown` / `on_startup` config flags onto the live service
- `EvalLoopSettingsSubscriber`: re-resolves the `hr.eval_loop_*` model / provider / mode keys and swaps the rebuilt pattern-identifier + fix-proposer strategies onto the live eval-loop coordinator
- `GithubApiUrlSettingsSubscriber`: re-binds `integrations.github_api_url` onto the GitHub health checker
- `ObservabilityBridgeSettingsSubscriber`: re-applies `audit_chain_signing_timeout_seconds` onto the live audit sink (plus the HTTP-log batch knobs)
- plus the per-domain bridge / live-config subscribers (api, workers, observability, security, tools, notifications, research, knowledge, simulations, ...) registered in `api/lifecycle_helpers/settings_dispatcher.py`

Settings resolved via `ConfigResolver` bridge configs (e.g. `get_communication_bridge_config()`) are re-fetched at the top of each polling iteration in their consumers, so operator changes take effect within one poll cycle without restart.

## Per-Operation Rate Limiting

Two layered rate-limit subsystems sit on top of the global three-tier
limiter (``api.rate_limit.*``). Both are runtime-editable via settings.

### Sliding-window guard (``api.per_op_rate_limit.*``)

| Setting | Type | Default | Runtime-editable | Purpose |
|---------|------|---------|------------------|---------|
| `api.per_op_rate_limit.enabled` | BOOLEAN | `true` | yes | Master switch; when `false` every `per_op_rate_limit` guard becomes a no-op. |
| `api.per_op_rate_limit.backend` | ENUM | `memory` | not settable | Sliding-window store backend, pinned to `memory`, the only shipped implementation; `redis` is reserved for cross-worker fairness. No registered setting writes it. |
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
| `api.per_op_concurrency.backend` | ENUM | `memory` | not settable | Inflight-counter store backend, pinned to `memory`; `redis` reserved. No registered setting writes it. |
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

Edit `coordination.ceremony_strategy` (`PUT /api/v1/settings/coordination/ceremony_strategy`). See [Ceremony Scheduling](../design/ceremony-scheduling.md) for the available strategies.

### Swap log sinks

Use `observability.custom_sinks` (JSON-typed) to add HTTP / syslog / OTLP shipping. See [Centralised Logging](centralized-logging.md) for examples.

---

## See Also

- [Company Configuration](company-config.md): YAML bootstrap config reference
- [Security Policies](security.md): autonomy, approvals
- [Centralised Logging](centralized-logging.md): log sink configuration
- [Design: Observability](../design/observability.md): architecture and event taxonomy
