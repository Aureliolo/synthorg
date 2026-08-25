---
title: Deployment (Docker)
description: Run SynthOrg in production with Docker, hardening, and image verification.
---

# Deployment (Docker)

SynthOrg runs as two Docker containers: a Python backend API and a Caddy + React web dashboard. This guide covers production deployment, environment configuration, security hardening, and operations.

---

## Architecture

```mermaid
graph LR
    User["Browser"]
    Web["web<br/><small>caddy:8080</small><br/><small>UID 65532</small>"]
    Backend["backend<br/><small>uvicorn:3001</small><br/><small>UID 65532</small>"]
    Volume["synthorg-data<br/><small>Files + Memory (+ SQLite)</small>"]
    Postgres["postgres<br/><small>:5432</small><br/><small>synthorg-pgdata</small>"]

    User -->|":3000"| Web
    Web -->|"/api/* proxy"| Backend
    Web -->|"/api/v1/ws proxy"| Backend
    Backend --> Volume
    Backend -->|"bundled default"| Postgres
```

The `synthorg-data` volume holds logs, artifacts, and agent memory (and the SQLite database file when SQLite is the backend). The bundled `docker/compose.yml` ships Postgres on a separate `synthorg-pgdata` volume; SQLite deployments omit the Postgres service.

| Container | Image | Purpose |
|-----------|-------|---------|
| **backend** | `ghcr.io/aureliolo/synthorg-backend` | Litestar API server (Wolfi apko-composed distroless, non-root) |
| **web** | `ghcr.io/aureliolo/synthorg-web` | Caddy + React 19 SPA (proxies API and WebSocket) |

---

## Quick Deploy

=== "CLI (recommended)"

    ```bash
    synthorg init     # interactive setup wizard
    synthorg start    # pull images, verify signatures, start containers
    synthorg status   # verify health
    ```

=== "Docker Compose (manual)"

    ```bash
    git clone https://github.com/Aureliolo/synthorg
    cd synthorg
    cp docker/.env.example docker/.env
    # Edit docker/.env with your secrets (see Environment Variables below)
    docker compose -f docker/compose.yml up -d
    ```

See the [Quickstart Tutorial](quickstart.md) for a complete walkthrough and the [User Guide](../user_guide.md) for all CLI commands.

---

## Environment Variables

All environment variables are configured in `docker/.env` (copy from `docker/.env.example`):

### Required

| Variable | Description |
|----------|-------------|
| `SYNTHORG_JWT_SECRET` | JWT signing secret. Must be >= 32 characters of URL-safe base64. Never commit to version control. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SYNTHORG_SETTINGS_KEY` | Fernet encryption key for sensitive settings at rest. Must be a valid Fernet key. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTHORG_DB_PATH` | `/data/synthorg.db` | SQLite database path (inside container) |
| `SYNTHORG_MEMORY_DIR` | `/data/memory` | Agent memory storage directory |
| `SYNTHORG_PERSISTENCE_BACKEND` | `postgres` | Compose-template selector only; the Python process does not read it. The backend is chosen by `SYNTHORG_DATABASE_URL` (Postgres) vs `SYNTHORG_DB_PATH` (SQLite). The bundled `docker/compose.yml` ships Postgres. |
| `SYNTHORG_MEMORY_BACKEND` | `sqlvector` | Registry override for the `memory.backend` setting (`sqlvector` / `composite` / `inmemory`). Restart-bound: the value is baked into the frozen memory config at startup. A database-stored setting wins over it. |
| `SYNTHORG_LOG_DIR` | `/data/logs` | Log file directory |
| `SYNTHORG_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error`, `critical` |
| `BACKEND_PORT` | `3001` | Host port for the backend API |
| `WEB_PORT` | `3000` | Host port for the web dashboard |
| `DOCKER_HOST` | *(unset)* | Docker socket for agent code execution sandbox (optional) |
| `SYNTHORG_TELEMETRY_ENABLED` | `false` | Enable opt-in anonymous product telemetry. Set to `true` / `1` / `yes` to enable; values like `false` / `0` / `no` keep it off. The Logfire project token is **embedded in the release wheel** at build time -- operators do not configure it. |
| `SYNTHORG_TELEMETRY_ENV` | *(unset)* | Explicit deployment-environment tag (`dev` / `pre-release` / `prod` / `ci` / `staging-east` / ...). Always wins the resolution chain if set. |
| `SYNTHORG_TELEMETRY_ENV_BAKED` | set by image | Image-baked fallback tag for the deployment environment. Release-tag CI builds bake `prod`; every pre-release tag form (`-dev.N`, `-rc.*`, `-alpha.*`, `-beta.*`) bakes `pre-release`; everything else bakes `dev`. Consulted only when `SYNTHORG_TELEMETRY_ENV` is unset *and* no CI markers are present; operators normally override via `SYNTHORG_TELEMETRY_ENV`. |

### Persistence, queue, and observability (CFG-1 audit)

These environment variables are read by the code but were previously undocumented. Setting any of them requires a container restart.

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTHORG_DATABASE_URL` | *(unset)* | Postgres connection URL (e.g. `postgres://user:pass@host:5432/synthorg`). Setting this switches the persistence backend from SQLite to Postgres regardless of `SYNTHORG_PERSISTENCE_BACKEND`. Query parameters are **not** supported in this URL; `_postgres_config_from_url()` rejects them up front; route `sslmode` overrides through `SYNTHORG_POSTGRES_SSL_MODE` instead. |
| `SYNTHORG_POSTGRES_SSL_MODE` | `require` | Override Postgres SSL mode (`disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`). When unset, the default comes from `PostgresConfig.ssl_mode` (`"require"`), which rejects plaintext connections. |
| `SYNTHORG_NATS_URL` | `nats://nats:4222` | NATS server URL for the distributed task queue. Required when `queue.enabled=true`. Must use `nats://`, `tls://`, or `nats+tls://`. |
| `SYNTHORG_NATS_STREAM_PREFIX` | `SYNTHORG` | JetStream stream name prefix. The bus stream is `<prefix>_BUS`; the KV bucket is `<prefix>_BUS_CHANNELS`. |
| `SYNTHORG_ARTIFACT_DIR` | `/data` (Postgres) or DB path directory (SQLite) | Filesystem path for artifact storage. Container deployments usually bind-mount this. |
| `SYNTHORG_TRACE_OTLP_ENDPOINT` | *(unset)* | OpenTelemetry OTLP HTTP endpoint (e.g. `http://otel-collector:4318/v1/traces`). Leaving it unset disables distributed tracing. |
| `SYNTHORG_TRACE_SERVICE_NAME` | `synthorg` | Service name attached to all emitted trace spans. |
| `SYNTHORG_TRACE_SAMPLING_RATIO` | `1.0` | Trace sampling ratio (0.0 = none, 1.0 = every request). |
| `SYNTHORG_CONFIG_PATH` | `company.yaml` | Path to the company configuration YAML file. Relative paths resolve against the working directory. |
| `SYNTHORG_WORKERS` | from config | Number of concurrent workers for the distributed task queue. Only consulted when the worker process is launched via `python -m synthorg.workers`. |

### Settings-registry env vars

Every registered setting automatically accepts an env-var override of the form `SYNTHORG_<NAMESPACE>_<KEY>`, where `<NAMESPACE>` and `<KEY>` are the registered setting's namespace and key uppercased (they are **not** derived from the setting's `yaml_path`). For example, setting `SYNTHORG_API_CORS_ALLOWED_ORIGINS='["http://localhost:5173"]'` overrides the CORS origin list for the current process. See [Settings Reference](settings-reference.md) for the full catalog.

**Resolution chain** (first match wins, in `synthorg.telemetry.collector._resolve_environment`):

1. `SYNTHORG_TELEMETRY_ENV` (operator override): always wins if non-empty.
2. CI auto-detection: `CI` / `GITLAB_CI` / `BUILDKITE` / `JENKINS_URL` / any `RUNPOD_*` present -> `"ci"`.
3. `SYNTHORG_TELEMETRY_ENV_BAKED` (image-baked fallback): set by CI via `DEPLOYMENT_ENV` build-arg; see above.
4. The parsed `TelemetryConfig.environment` value, which itself defaults to `"dev"` when not configured.

### Image build-args

| Build arg | Default | Description |
|-----------|---------|-------------|
| `DEPLOYMENT_ENV` | `dev` | Baked deployment-environment tag (`dev` / `pre-release` / `prod`). CI computes and passes this automatically; local `docker build` without `--build-arg` inherits `dev`. |

---

## First-Run Setup

After the containers are running, open `http://localhost:3000`. The setup wizard appears on a fresh install. See the [User Guide](../user_guide.md#first-run-setup) for the full wizard walkthrough.

---

## Health Probes

Three distinct health endpoints live in this deployment; don't conflate them.

| Endpoint | Layer | Purpose | Behaviour |
|----------|-------|---------|----------|
| `GET /api/v1/healthz` | Backend (API) | Liveness | Always 200 while the API process is alive. Fails only on process death. Use for **container restart policies** (`docker compose` `healthcheck`, Kubernetes `livenessProbe`). |
| `GET /api/v1/readyz` | Backend (API) | Readiness | 200 when every configured dependency this process owns (persistence, message bus, a wired memory substrate) is healthy; 503 otherwise. Reachability of a third-party LLM provider is reported on `/health`, never gated on: every replica reaches the same endpoint, so draining on it would drain every replica at once, taking down the very dashboard an operator would use to repoint the broken provider. Body is topology-free (binary outcome + uptime). Use for **load-balancer drain** and `docker compose` readiness gates (`depends_on.condition: service_healthy`). |
| `GET /api/v1/health` | Backend (API) | Component detail | Authenticated per-component breakdown (persistence / message bus / providers / telemetry / backup) plus the build version; requires a read-access role. For operator dashboards, not orchestrator probes. |
| `GET /healthz` | Web (Caddy) | Liveness | Caddy's built-in endpoint, served by the static-asset container. Reports that Caddy is accepting HTTP; distinct from the backend's `/api/v1/healthz`. |

The probe endpoints (`/api/v1/healthz`, `/api/v1/readyz`) are unauthenticated so load-balancers and container orchestrators can probe them without credentials. Because they are, they disclose neither the component topology nor the build version: an exact version reveals to an anonymous caller precisely which published advisories apply, and no orchestrator decision depends on it. Read the running version from the authenticated `/api/v1/health`, or locally from the deployed image tag (`synthorg status`). All are pinned in the OpenAPI schema.

---

## Container Details

### Backend

- **Base image**: Wolfi apko-composed distroless (no shell, continuously scanned)
- **Build**: 2-stage (builder -> apko runtime) for minimal attack surface
- **User**: UID 65532 (distroless non-root)
- **Health check**: `GET /api/v1/healthz` (10s interval, 5s timeout, 3 retries, 600s start period, 5s start interval), declared in `docker/backend/Dockerfile` and never overridden at the compose layer. Liveness, not readiness: the only consumer is the `depends_on: service_healthy` gate in Compose that holds `web` back until the API answers, and probing `/readyz` would tie the dashboard availability to Postgres and NATS health. The start period covers the whole cold boot, because uvicorn runs the entire ASGI lifespan (persistence connect, migrations, service wiring) before it binds the listening socket, so nothing answers until boot completes. It is derived from measurement (`scripts/measure_backend_boot.sh`) and CI fails a build whose boot grows into its headroom.
- **Entry point**: `uvicorn synthorg.api.app:create_app --factory --no-access-log`

### Web

- **Base image**: Pure apko Wolfi (Caddy + melange-packaged static assets, no Dockerfile)
- **User**: UID 65532 (caddy)
- **Health check**: `GET /healthz` via Caddy, Caddy's own built-in liveness endpoint (distinct from the backend's `/api/v1/healthz`, see [Health Probes](#health-probes) above). Compose-level probe using `wget`; 10s interval, 3s timeout, 3 retries, 10s start period. The apko image intentionally ships no Dockerfile `HEALTHCHECK`, so the probe is declared alongside the service and targets `127.0.0.1` to avoid Docker DNS.
- **Routing**: SPA routing (`try_files {path} /index.html`), API proxy to backend, WebSocket proxy, per-request CSP nonce via Caddy `templates` directive
- **Caching**: `/index.html` is no-cache; `/assets/*` is immutable with 1-year max-age (content-hashed filenames)
- **Static compression**: pre-compressed `.gz` files served via `file_server { precompressed gzip }`

---

## Security Hardening

The Docker Compose configuration follows the [CIS Docker Benchmark v1.6.0](https://www.cisecurity.org/benchmark/docker):

| Control | Setting | CIS Reference |
|---------|---------|---------------|
| No new privileges | `security_opt: [no-new-privileges:true]` | 5.3 |
| Drop all capabilities | `cap_drop: [ALL]` | 5.12 |
| Read-only root filesystem | `read_only: true` + tmpfs mounts | 5.25 |
| PID limits | 256 (backend), 64 (web) | 5.28 |
| Memory limits | 4G (backend), 256M (web) | - |
| CPU limits | 2.0 (backend), 0.5 (web) | - |
| Log rotation | json-file, 10MB max, 3 files | - |
| Tmpfs security | `noexec,nosuid,nodev` on `/tmp` | - |

### Security Headers (Caddy)

The web container sets the following response headers:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), camera=(), microphone=()`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{http.request.uuid}' 'unsafe-inline'; style-src-elem 'self' 'nonce-{http.request.uuid}'; style-src-attr 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`
- `Strict-Transport-Security: max-age=63072000` (2 years)

The CSP uses Level 3 directive splitting: `style-src-elem` locks `<style>` elements to the per-request nonce (injected by Caddy's `templates` directive substituting `{http.request.uuid}` into `<meta name="csp-nonce">`), while `style-src-attr 'unsafe-inline'` covers the transient inline positioning styles set by Floating UI (used internally by Base UI). See [`docs/security.md` → CSP Nonce Infrastructure](../security.md#csp-nonce-infrastructure) for the full flow; any reverse proxy in front of the web container must preserve Caddy's template substitution and the matching CSP header, otherwise inline styles will be blocked.

---

## Volumes & Data Persistence

The `synthorg-data` Docker volume persists all application data:

- SQLite database (`/data/synthorg.db`)
- Agent memory files (`/data/memory/`)
- Log files (`/data/logs/`)

### Backup

```bash
synthorg backup                          # create a backup
synthorg backup list                     # list available backups
synthorg backup restore <id> --confirm   # restore from a backup
```

For manual Docker Compose deployments, back up the `synthorg-data` volume directly.

### Wipe & Reset

```bash
synthorg wipe    # offers backup, wipes all data, optionally restarts fresh
```

---

## Networking

Both containers run on the `synthorg-net` Docker network. The web container proxies API requests to the backend:

- `http://localhost:3000/api/*` -> `http://backend:3001/api/*`
- `ws://localhost:3000/api/v1/ws` -> `ws://backend:3001/api/v1/ws`

### Fine-Tuning (optional)

Embedding fine-tuning runs in a dedicated ephemeral container that the backend spawns on demand. It is **disabled by default** because the image is large and the workload is heavy.

Two image variants ship from GHCR, both amd64-only:

| Image | Torch | Size | When to pick |
|-------|-------|------|--------------|
| `ghcr.io/aureliolo/synthorg-fine-tune-gpu` | bundled CUDA (`torch==2.13.0`) | ~4 GB download (~7 GB on disk) | Host has an NVIDIA GPU + compatible driver; practical training speed |
| `ghcr.io/aureliolo/synthorg-fine-tune-cpu` | CPU-only (`torch==2.13.0+cpu` via `download.pytorch.org/whl/cpu`) | ~1.7 GB | Host has no GPU; correctness-first, training is slower |

Fine-tuning also requires the sandbox to be enabled (`sandbox=true`). The backend launches each pipeline stage in a one-shot container using the Docker API.

=== "CLI (post-install)"

    Enable on an existing install without wiping data:

    ```bash
    synthorg config set sandbox true
    synthorg config set fine_tuning true
    synthorg config set fine_tuning_variant gpu   # or: cpu
    synthorg stop && synthorg start               # compose.yml is regenerated automatically
    ```

    `synthorg init` also prompts for this, but only use `init` on a **fresh** data dir; it overwrites `config.json` and regenerates `compose.yml`. Existing installs should use `config set` as above.

=== "Docker Compose (manual / BYO)"

    In a hand-managed `compose.yml`, wire the fine-tune image into the backend's environment. There is no standing fine-tune service: the backend spawns an ephemeral one-shot container per torch-bound stage (and an on-demand readiness probe) from this image and removes it on exit.

    ```yaml
    services:
      backend:
        environment:
          # Backend reads this on demand to spawn ephemeral fine-tune
          # stage containers via the Docker API. Point at a digest-pinned
          # ref for reproducibility.
          SYNTHORG_FINE_TUNE_IMAGE: ghcr.io/aureliolo/synthorg-fine-tune-gpu:${SYNTHORG_IMAGE_TAG:-latest}
          # For CPU-only hosts, swap to: ghcr.io/aureliolo/synthorg-fine-tune-cpu
    ```

    Image signatures can be verified out-of-band with `cosign verify` and SLSA provenance with `gh attestation verify oci://...`; the CLI-generated compose pins digests automatically. See [Image Verification](#image-verification) below.

### Local LLM Providers

To use a local LLM like Ollama running on the host machine, configure the provider with `host.docker.internal`:

```yaml
providers:
  local-ollama:
    auth_type: none
    base_url: "http://host.docker.internal:11434"
```

This is the **backend** container reaching a service on the host, and on Linux
Engine it needs the alias to exist: Docker Desktop injects
`host.docker.internal`, plain Engine does not. Add it to the backend service
yourself there:

```yaml
services:
  backend:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Do not confuse it with the same hostname in an address handed to a spawned
container. Those point the other way, back at the API's published port, and
such a container is given the alias explicitly by whatever spawns it, so it
resolves on Engine too without any host configuration.

---

## Image Verification

SynthOrg container images are signed with [cosign](https://docs.sigstore.dev/cosign/) keyless signatures and include [SLSA Level 3](https://slsa.dev/) provenance attestations.

`synthorg start` and `synthorg update` automatically verify signatures before pulling images. If verification fails (e.g. in an air-gapped environment):

```bash
synthorg start --skip-verify
# or
export SYNTHORG_SKIP_VERIFY=1
synthorg start
```

---

## Updates

```bash
synthorg update    # pull latest images, verify signatures, restart containers
```

The CLI re-launches itself after binary replacement so the remaining steps use the new version. If the compose template has structural changes, the diff is shown for approval before applying.

### Channels

| Channel | Description |
|---------|-------------|
| `stable` | Stable releases only (default) |
| `dev` | Pre-release builds on every push to main |

```bash
synthorg config set channel dev      # opt in to pre-release builds
synthorg config set channel stable   # switch back to stable
```

### Auto-Cleanup

Automatically remove old container images after updates (keeps current + previous version):

```bash
synthorg config set auto_cleanup true
```

---

## Production Checklist

!!! info "Production readiness checklist"

    - [ ] Generate strong secrets for `SYNTHORG_JWT_SECRET` and `SYNTHORG_SETTINGS_KEY`
    - [ ] Set `SYNTHORG_LOG_LEVEL` to `warning` or `info` (not `debug`)
    - [ ] Review and set appropriate `BACKEND_PORT` and `WEB_PORT`
    - [ ] Configure budget limits to prevent runaway LLM costs
    - [ ] Set autonomy level to `semi` or `supervised` (not `full`) for production orgs
    - [ ] Enable security audit logging (`security.audit_enabled: true`)
    - [ ] Set up backup schedule (`synthorg backup`)
    - [ ] Place behind a reverse proxy with TLS termination
    - [ ] Restrict Docker socket access if using the sandbox feature
    - [ ] Monitor container health via `synthorg status` or Docker health checks

---

## Troubleshooting

### Health Check

```bash
synthorg doctor    # run diagnostics
synthorg status    # check container health
synthorg logs      # view container logs
```

### Common Issues

| Issue | Solution |
|-------|----------|
| Backend container keeps restarting | Check `synthorg logs` for startup errors. Verify `SYNTHORG_JWT_SECRET` and `SYNTHORG_SETTINGS_KEY` are set. |
| Dashboard shows "Connection refused" | Ensure the web container is healthy and `WEB_PORT` is not in use. |
| Image pull fails | Check network connectivity. If air-gapped, use `--skip-verify`. |
| "Port already in use" | Change `BACKEND_PORT` or `WEB_PORT` in `docker/.env`. |
| Ollama not connecting | Use `http://host.docker.internal:11434` as the base URL. |

---

## See Also

- [Quickstart Tutorial](quickstart.md): get started in 5 minutes
- [User Guide](../user_guide.md): CLI commands and setup wizard
- [Security](../security.md): security architecture reference
- [Company Configuration](company-config.md): full configuration reference
