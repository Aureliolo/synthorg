# Environment Variables

On-demand reference for the `SYNTHORG_*` environment variables the
backend reads.  The precedence rule that governs **mutable** settings
is in [configuration-precedence.md](configuration-precedence.md); the
short version is `DB > env > default`.  Variables in this page fall
into three categories:

- **Init-time only:** read once at process start; no registry entry.
  Operator changes require a process restart.  Used for credentials
  and bootstrap-only paths where a runtime registry entry would be
  unsafe.
- **Init-time, registry for discoverability:** read once at boot but
  also exposed in the /settings UI through a `read_only_post_init`
  registry entry so operators can introspect the running value.
  `SettingsService.set()` rejects mutation with `SettingReadOnlyError`.
- **Runtime override:** `SYNTHORG_<NAMESPACE>_<KEY>` derived from the
  registry definition; consulted on every resolve through
  `SettingsService.get()`.  These are the standard mutable settings.

## Persistence (init-time only)

| Variable | Default | Purpose |
|---|---|---|
| `SYNTHORG_DATABASE_URL` | unset | Postgres connection URL.  Format `postgresql://user:pass@host:port/db`.  No query parameters allowed; use `SYNTHORG_POSTGRES_SSL_MODE` for ssl overrides. |
| `SYNTHORG_DB_PATH` | unset | SQLite database file path.  Mutually exclusive with `SYNTHORG_DATABASE_URL` -- if both are set, Postgres wins.  Consumed by `synthorg.api.app:create_app`. |
| `SYNTHORG_POSTGRES_SSL_MODE` | unset (driver default) | Optional override for the Postgres SSL mode (`disable` / `require` / `verify-ca` / `verify-full`).  Validated at startup. |

## Bootstrap secrets (init-time only)

| Variable | Default | Purpose |
|---|---|---|
| `SYNTHORG_JWT_SECRET` | unset | JWT signing secret.  Required for multi-instance deployments so a token issued by one replica verifies on another.  Consumed by `synthorg.api.auth.secret`. |
| `SYNTHORG_MASTER_KEY` | unset | Master key for the encrypted secret backends and integration credential storage.  Consumed by `synthorg.persistence.secret_backends`. |
| `SYNTHORG_PAGINATION_CURSOR_SECRET` | unset | HMAC secret for paginated cursor signing.  Falls back to a process-ephemeral secret (cursors invalidate on restart) when unset. |

## Filesystem (init-time, some with registry discoverability)

| Variable | Default | Registry key | Purpose |
|---|---|---|---|
| `SYNTHORG_LOG_DIR` | unset | `observability/log_directory` (read-only) | Log output directory.  Path-traversal rejected at boot. |
| `SYNTHORG_ARTIFACT_DIR` | `/data` | n/a | Filesystem artifact storage root.  Must be absolute and free of `..` components. |
| `SYNTHORG_MEMORY_DIR` | tmp fallback | n/a | On-disk memory backend root for the local Mem0 backend. |
| `SYNTHORG_CONFIG_PATH` | `company.yaml` | n/a | Path to the company YAML config used by the backup factory. |

## Sandbox / fine-tune images (init-time only)

| Variable | Default | Purpose |
|---|---|---|
| `SYNTHORG_SANDBOX_IMAGE` | `ghcr.io/aureliolo/synthorg-sandbox:latest` | Sandbox container image; CLI sets the digest-pinned variant after cosign verification. |
| `SYNTHORG_SIDECAR_IMAGE` | `ghcr.io/aureliolo/synthorg-sidecar:latest` | Sidecar (network-proxy) container image. |
| `SYNTHORG_FINE_TUNE_IMAGE` | unset | Override for the embedding fine-tune image (CLI publishes `-gpu` and `-cpu` variants). |
| `SYNTHORG_FINE_TUNE_HEALTH_PORT` | `15002` | Port the fine-tune container's health probe listens on. |

## Telemetry (runtime override)

| Variable | Default | Registry key | Purpose |
|---|---|---|---|
| `SYNTHORG_TELEMETRY_ENABLED` | unset | `telemetry/enabled` | Master opt-in for product telemetry.  Accepts `true` / `false` / `1` / `0` / `yes` / `no`. |
| `SYNTHORG_TELEMETRY_ENV` | unset | n/a | Operator override for the deployment environment tag (`prod` / `dev` / `pre-release` / custom).  Wins over CI auto-detection and the Dockerfile-baked default. |
| `SYNTHORG_TELEMETRY_ENV_BAKED` | (image-baked) | n/a | Dockerfile-baked deployment environment.  CI sets this in published images; operators normally don't touch it. |

## Tracing (init-time only)

| Variable | Default | Purpose |
|---|---|---|
| `SYNTHORG_TRACE_OTLP_ENDPOINT` | unset | OTLP collector endpoint for distributed traces.  When unset the OTLP exporter is not wired. |
| `SYNTHORG_TRACE_SERVICE_NAME` | `synthorg` | Service name attached to spans. |
| `SYNTHORG_TRACE_SAMPLING_RATIO` | `1.0` | Probabilistic span sampler ratio (0.0–1.0). |

## NATS (init-time, registry for discoverability)

| Variable | Default | Registry key | Purpose |
|---|---|---|---|
| `SYNTHORG_NATS_URL` | `nats://nats:4222` | `communication/nats_url` (read-only) | NATS server URL.  Bus driver opens its connection once at boot. |
| `SYNTHORG_DEFAULT_NATS_URL` | unset | n/a | Compose-template default that flows into `communication.nats.url` when no operator value is set. |

## Logging (mutable runtime override)

| Variable | Default | Registry key | Purpose |
|---|---|---|---|
| `SYNTHORG_LOG_LEVEL` | unset | `observability/log_level_console` (mutable) | Override the console sink's log level distinct from the root logger.  Standard Cat-1 chain (`DB > env > default`).  Applied at boot via `_apply_console_level_override`; runtime mutation through the registry takes effect on the next subscriber-driven rebuild. |

## Workers (init-time, registry for discoverability)

| Variable | Default | Registry key | Purpose |
|---|---|---|---|
| `SYNTHORG_WORKERS` | `1` | `workers/count` (read-only) | Uvicorn worker process count. |

## Generic registry override

For every **mutable** setting registered in
`src/synthorg/settings/definitions/`, the env-var override is
auto-derived as:

```text
SYNTHORG_<NAMESPACE>_<KEY>
```

where the namespace and key are the registry tuple (uppercased,
underscore-joined).  A handful of settings carry a custom
`env_var_override` that supersedes the auto-derived name (see the
table at [configuration-precedence.md](configuration-precedence.md)
§ "Custom env var names"); for those entries, only the override
name is consulted.  Examples of the auto-derived shape:

| Registry key | Auto-derived env var |
|---|---|
| `api/sse_keepalive_seconds` | `SYNTHORG_API_SSE_KEEPALIVE_SECONDS` |
| `engine/evolution_enabled` | `SYNTHORG_ENGINE_EVOLUTION_ENABLED` |
| `coordination/department_policy_cas_retry_attempts` | `SYNTHORG_COORDINATION_DEPARTMENT_POLICY_CAS_RETRY_ATTEMPTS` |

The override sits below the DB and above the registered default in
the precedence chain (see
[configuration-precedence.md](configuration-precedence.md)).
For a complete inventory of registered settings see the schema endpoint
`GET /api/v1/settings/schema` or the `src/synthorg/settings/definitions/`
directory.

## Adding a new env var

1. If the variable maps to a **mutable** setting: register a
   `SettingDefinition` and the auto-derived env var is wired
   automatically.  No change to this page is required.
2. If the variable is **init-time only** (credentials / bootstrap):
   add a row to one of the sections above and consume the value with
   `os.environ.get` at the relevant boot site.  Document the consumer
   module path in the row.
3. If the variable should be **discoverable but read-only**: register
   with `restart_required=True` and `read_only_post_init=True`, link
   the registry key in this page, and consume via `os.environ.get` at
   the boot site (the registry entry just exposes the value to the
   /settings UI).
