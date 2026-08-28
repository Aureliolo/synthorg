# Telemetry (Product)

On-demand reference for product telemetry. The short rule in `CLAUDE.md` is: telemetry is opt-in and off by default; every event property must be explicitly allowlisted; never bypass the scrubber.

## Enabling

- Off by default. Enable with `SYNTHORG_TELEMETRY_ENABLED=true` or the `telemetry.enabled` setting (or via the dashboard / DB).
- Delivery backend is Logfire. The write-only project token is **embedded in the published backend Docker image** at build time: `docker/backend/Dockerfile`'s builder stage runs `scripts/embed_logfire_token.py`, rewriting `src/synthorg/telemetry/reporters/_embedded_token.py` in place before `uv sync` packages the venv, gated on the optional `LOGFIRE_PROJECT_TOKEN` build-arg that `.github/workflows/build-images.yml` supplies from the GHA repo secret (pull-request builds deliberately receive an empty token, so unreviewed PR code never gets the write-only secret). Operators never configure or paste it. Local source installs and any build where the build-arg is unset ship the sentinel and run telemetry-disabled by build.

## Lifecycle log signals

The collector emits **exactly one** lifecycle log line per process boot, depending on which path applies:

| State | Severity | Event | Notes |
|-------|----------|-------|-------|
| Disabled (default) | INFO | `telemetry.disabled` | Heartbeat task not started; zero per-cycle warnings. |
| Enabled, embedded token present | INFO | `telemetry.reporter.initialized` | Heartbeat starts; `deployment.startup` follows. |
| Enabled, sentinel token (build artifact missing token) | ERROR | `telemetry.token.missing` | Operator-actionable: rebuild with `LOGFIRE_PROJECT_TOKEN` CI secret. Heartbeat NOT started -- no per-cycle spam. |
| Enabled, `logfire` import or configure failure | WARNING | `telemetry.report.failed` (factory) | `error_type` carries the actual exception class (no longer hardcoded as `ImportError`). Heartbeat NOT started. |

## Privacy by allowlist

Every event property must be explicitly listed in `src/synthorg/telemetry/privacy.py::_ALLOWED_PROPERTIES` keyed by event type. Unknown keys raise `PrivacyViolationError` and are dropped before delivery.

Forbidden key patterns (rejected even if allowlisted):

- `key`, `token`, `secret`, `password`, `credential`, `bearer`, `auth`
- `content`, `message`, `prompt`, `description`

String values are capped at `synthorg.telemetry.config.MAX_STRING_LENGTH` (64 chars).

## Collector lifecycle

Two-phase to keep filesystem I/O off the event loop:

1. **Construction** (`__init__`): reads config and resolves the environment tag. Performs zero filesystem I/O. Safe to call synchronously from the app builder. The `enabled` flag here is resolved env > default only (persistence is not yet connected).
2. **DB-layer re-resolve** (`apply_enabled`, boot only): when the collector is booted through the Litestar lifecycle, the `_apply_telemetry_db_layer` startup hook re-resolves `telemetry.enabled` with full DB > env > default precedence once the config resolver is wired, then calls `apply_enabled(enabled=...)` to rebind the frozen config BEFORE `start()` reads it. Without this a DB-stored `telemetry.enabled` toggle would be ignored on restart.
3. **Startup** (`async start()`): loads or creates the anonymous deployment ID via `asyncio.to_thread` (5 s hard deadline). Sends the initial `deployment.startup` event with Docker enrichment. Schedules the periodic heartbeat task. Idempotent under concurrent callers via the lifecycle lock; the second caller finds `_deployment_id` already populated and skips the load.
4. **Shutdown** (`async shutdown()`): cancels the heartbeat, sends `deployment.session_summary` and `deployment.shutdown`, then shuts down the reporter. Skips event emission if `_deployment_id` was never loaded (and logs `telemetry.shutdown.without_start` so operators can detect a silent init failure).

The deployment ID lives at `{data_dir}/telemetry_id`. Multiple replicas mounting the same `/data` race on `O_CREAT|O_EXCL`; losers re-read the winner's UUID with retry on partial-write windows so all replicas converge to one ID. On read/write failure the collector falls back to an in-memory UUID and tags the warning log with `using_generated_id=True` so dashboards can detect splinter deployments.

## Environment resolution chain

First match wins, implemented in `synthorg.telemetry.collector._resolve_environment`:

1. `SYNTHORG_TELEMETRY_ENV` (operator override; always wins).
2. CI auto-detection: `CI`, `GITLAB_CI`, `BUILDKITE`, `JENKINS_URL`, any `RUNPOD_*` → `"ci"`.
3. `SYNTHORG_TELEMETRY_ENV_BAKED` (Dockerfile `ARG DEPLOYMENT_ENV` baked at build; CI sets `prod` / `pre-release` / `dev`).
4. `TelemetryConfig.environment` (default `"dev"`).

## Docker daemon enrichment

At startup via `synthorg.telemetry.host_info.fetch_docker_info` (uses `aiodocker`). Requires `/var/run/docker.sock` bind-mounted (sandbox overlay / `synthorg init --sandbox true`).

Allowlisted fields:
`docker_server_version`, `docker_operating_system`, `docker_os_type`, `docker_os_version`, `docker_architecture`, `docker_kernel_version`, `docker_storage_driver`, `docker_default_runtime`, `docker_isolation`, `docker_ncpu`, `docker_mem_total`, `docker_gpu_runtime_nvidia_available`.

When the socket isn't mounted or the daemon is unreachable, the event carries `docker_info_available=False` + a categorical `docker_info_unavailable_reason`.

## Adding a new event or property

Keep in sync:

1. `host_info._extract()` (if sourcing from host info).
2. The `DockerHostInfo` TypedDict (if a Docker field).
3. The scrubber's `_ALLOWED_PROPERTIES` entry for the event type.

New allowlisted keys must not match a forbidden pattern. Never bypass the scrubber.
