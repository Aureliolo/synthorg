---
title: CLI Environment Variables
description: Full SYNTHORG_* env var inventory consumed by the CLI binary or written by the CLI into the generated compose.yml, including timeouts, retry counts, image overrides, and the hardcoded-network-literal audit rationale.
---

# CLI Environment Variables

On-demand reference. The short list in `cli/CLAUDE.md` is the env-var-only settings (no corresponding flag). This page is the full inventory plus the audit rationale for hardcoded network literals.

## Env-var-only settings

The "Used by" column distinguishes three relationships to the CLI:

- **CLI**: read directly by the Go CLI binary at flag-resolution / config-load time.
- **CLI -> compose**: written by the CLI into the generated `compose.yml` for a backend or sidecar container; the CLI itself does not consult the value at runtime.
- **container**: read directly inside a container at runtime; the CLI neither reads nor writes it. Listed here for operator visibility.

| Env Var | Used by | Description |
|---------|---------|-------------|
| `SYNTHORG_DATA_DIR` | CLI | Override the CLI data directory (below the `--data-dir` flag, above the platform default: `%LOCALAPPDATA%\synthorg` on Windows, `$XDG_DATA_HOME/synthorg` or `~/.local/share/synthorg` on Linux, `~/Library/Application Support/synthorg` on macOS) |
| `SYNTHORG_SKIP_VERIFY` | CLI | Env equivalent of the `--skip-verify` flag; skips image signature verification (truthy: `1` / `true` / `yes`, case-insensitive) |
| `SYNTHORG_QUIET` | CLI | Env equivalent of the `--quiet` / `-q` flag (truthy: `1` / `true` / `yes`) |
| `SYNTHORG_YES` | CLI | Env equivalent of the `--yes` / `-y` flag; suppresses all interactive confirmation prompts (truthy: `1` / `true` / `yes`) |
| `SYNTHORG_LOG_LEVEL` | CLI | Override backend log level |
| `SYNTHORG_BACKEND_PORT` | CLI | Override backend API port |
| `SYNTHORG_WEB_PORT` | CLI | Override web dashboard port |
| `SYNTHORG_CHANNEL` | CLI | Override release channel (stable / dev) |
| `SYNTHORG_IMAGE_TAG` | CLI | Override container image tag |
| `SYNTHORG_AUTO_UPDATE_CLI` | CLI | Auto-accept CLI self-updates; the changelog is printed rather than paged |
| `SYNTHORG_AUTO_PULL` | CLI | Auto-accept container image pulls |
| `SYNTHORG_AUTO_RESTART` | CLI | Auto-restart containers after update |
| `SYNTHORG_TELEMETRY_ENABLED` | CLI | Enable anonymous project telemetry (true / false) |
| `SYNTHORG_FINE_TUNE_IMAGE` | CLI -> compose | Fine-tune container image ref read by the backend. The CLI writes the variant-specific verified image (`synthorg-fine-tune-gpu` or `synthorg-fine-tune-cpu`) into the generated `compose.yml`, chosen via `synthorg init` and persisted as `fine_tuning_variant` in `config.json`. The CLI does not read this var at runtime; manual operator overrides bypass CLI signature / provenance verification and are not supported. |
| `SYNTHORG_TUNNEL_STATE_DIR` | CLI -> compose | Tunnel runtime state root read by the backend (downloaded provider binaries, the confined login home for the `devtunnel` CLI). The generated `compose.yml` sets `/data/tunnel` unconditionally so tunnel state survives container recreation; the CLI does not read this var at runtime. |
| `SYNTHORG_REGISTRY_HOST` | CLI | Override default container registry hostname (disables verification when set) |
| `SYNTHORG_IMAGE_REPO_PREFIX` | CLI | Override default image repository prefix (disables verification when set) |
| `SYNTHORG_DHI_REGISTRY` | CLI | Override Docker Hardened Images registry (disables verification when set) |
| `SYNTHORG_POSTGRES_IMAGE_TAG` | CLI | Override pinned Postgres DHI tag (disables verification when set). Tag default lives in `cli/internal/config/state.go::DefaultPostgresImageTag`; matching multi-arch index digest is the sibling `DefaultPostgresImageDigest` constant in the same file (single source of truth, kept current by one Renovate customManager that captures tag+digest together). `cli/internal/verify/dhi.go::dhiPinnedIndexDigests` derives from these constants at init. Renovate's docker-compose manager is disabled on `docker/compose.yml`, so any PR bumping the canonical tag/digest MUST hand-mirror the matching `image:` line in `docker/compose.yml` in the same commit; `cli/internal/verify/compose_sync_test.go` enforces this. |
| `SYNTHORG_NATS_IMAGE_TAG` | CLI | Override pinned NATS DHI tag (disables verification when set). Tag default lives in `cli/internal/config/state.go::DefaultNATSImageTag`; matching multi-arch index digest is the sibling `DefaultNATSImageDigest` constant in the same file. Same hand-mirror constraint to `docker/compose.yml` as `SYNTHORG_POSTGRES_IMAGE_TAG`. |
| `SYNTHORG_NATS_URL` | CLI | Override `synthorg worker start --nats-url` default (single source of truth shared with the backend's `communication.nats_url`) |
| `SYNTHORG_DEFAULT_NATS_STREAM_PREFIX` | CLI | Override `synthorg worker start --stream-prefix` default |
| `SYNTHORG_BACKUP_CREATE_TIMEOUT` | CLI | Override `synthorg backup create --timeout` default (duration, e.g. `60s`) |
| `SYNTHORG_BACKUP_RESTORE_TIMEOUT` | CLI | Override `synthorg backup restore --timeout` default |
| `SYNTHORG_HEALTH_CHECK_TIMEOUT` | CLI | Per-request HTTP timeout for health endpoint probes (duration, default `5s`) |
| `SYNTHORG_HEALTH_WAIT_TIMEOUT` | CLI | Total readiness-wait budget; default for `start --timeout` and the `wipe` reinit health wait (duration, default `90s`) |
| `SYNTHORG_SELF_UPDATE_HTTP_TIMEOUT` | CLI | HTTP timeout for CLI binary download (duration, default `5m`) |
| `SYNTHORG_SELF_UPDATE_API_TIMEOUT` | CLI | Overall budget for GitHub API metadata fetches, shared across the transient-failure retries (a 5xx or network blip is retried a few times with exponential backoff within this budget) (duration, default `30s`) |
| `SYNTHORG_TUF_FETCH_TIMEOUT` | CLI | HTTP timeout for Sigstore TUF trusted root fetch (duration, default `30s`) |
| `SYNTHORG_ATTESTATION_HTTP_TIMEOUT` | CLI | HTTP timeout for GitHub attestation API requests and `bundle_url` fetches (duration, default `30s`) |
| `SYNTHORG_MAX_API_RESPONSE_BYTES` | CLI | Maximum bytes for API / checksum downloads (default `4MiB`; accepts `1MiB`, `1048576`). Sized for the list-commits walk used by `synthorg update`: each commit object inlines the full PGP signature plus signed-payload duplicate plus 20+ author / committer URL fields (~15 KiB / commit), so a typical 25-entry page is ~400 KiB and 4 MiB gives 10x headroom. Hard ceiling is 1 GiB via `MaxBytesCeiling`. |
| `SYNTHORG_MAX_BINARY_BYTES` | CLI | Maximum bytes for CLI binary archive downloads (accepts `256MiB`) |
| `SYNTHORG_MAX_ARCHIVE_ENTRY_BYTES` | CLI | Maximum bytes per archive entry during extraction (accepts `128MiB`) |
| `SYNTHORG_IMAGE_VERIFY_TIMEOUT` | CLI | Context timeout for the cosign + SLSA verification pass during `start` and `update`. Duration, default `120s`, hard minimum `1s` (shorter values would bypass verification by silently timing out before cosign / SLSA / TUF completes network I/O). |
| `SYNTHORG_IMAGE_PULL_ATTEMPTS` | CLI | Retry count for transient `docker pull` failures on standalone images (integer in `[1, 100]`, default `3`) |
| `SYNTHORG_IMAGE_PULL_RETRY_DELAY` | CLI | Base backoff between pull retries. Exponential: N-th retry waits `delay * 2^(N-1)` seconds (e.g. `2s` base produces 2s, 4s, 8s, 16s, ...), saturated at a 5 min ceiling to guard against overflow when `image_pull_attempts` is large. Duration, default `2s`. |
| `SYNTHORG_HEALTH_POLL_INTERVAL` | CLI | Interval between backend `/readyz` health polls during `start` (duration, default `2s`) |
| `SYNTHORG_HEALTH_INITIAL_DELAY` | CLI | Delay before the first `/readyz` poll during `start`, skipping the cold compose-up window (duration, default `5s`) |
| `SYNTHORG_DHI_VERIFY_TIMEOUT` | CLI | Context timeout for the per-batch DHI cosign + SLSA verification during `start` (duration, default `120s`) |
| `SYNTHORG_UPDATE_HEALTH_TIMEOUT` | CLI | Timeout for the Docker API calls the `update` flow makes to inspect the current install (duration, default `15s`) |
| `SYNTHORG_COMPLETION_PROBE_TIMEOUT` | CLI | Timeout for the one-shot shell-profile probe run by `synthorg completion install` (duration, default `5s`) |
| `SYNTHORG_DIAGNOSTICS_DIAL_TIMEOUT` | CLI | Per-port TCP dial timeout in the `synthorg doctor` port-reachability check (duration, default `1s`) |
| `SYNTHORG_STATUS_DOCKER_TIMEOUT` | CLI | Timeout for the Docker API calls `synthorg status` makes for the resource-usage and Postgres-volume sections (duration, default `15s`) |

## Hardcoded network literals (audit rationale)

The CLI contains several `localhost` / service-DNS / port literals that look non-configurable but are correct by design:

- **`localhost` in `doctor.go` / `start.go` / `status_render.go` / `status_snapshot.go` / `wipe.go` / `update_restart.go` / `update_compose.go` / `backup.go`**: these print or construct URLs pointing at the operator's own host (e.g. `http://localhost:<BackendPort>/api/v1/readyz`). The port is flag / env-driven (`SYNTHORG_BACKEND_PORT`, `SYNTHORG_WEB_PORT`); the hostname is literally the host the CLI is running on.
- **`localhost` / `127.0.0.1` in `helpers.go::openBrowser`**: not a printed URL but a host allowlist. The function refuses to hand any URL whose host isn't `localhost` or `127.0.0.1` to the OS browser launcher, so it cannot be made to open an arbitrary URL.
- **`postgres:5432` in `compose/generate.go::pgDSN`**: docker-compose internal DNS, container-to-container. The host-side Postgres port is a separate `Params.PostgresPort` tunable rendered in `compose.yml.tmpl`.
- **NATS client/monitoring ports**: `NATSClientPort = 4222` and `NATSHTTPPort = 8222` are Go constants in `compose/nats_config.go`, consumed both to render the generated `nats.conf` (`port: 4222`, `http_port: 8222`) and as the in-container side of the port mapping in `compose.yml.tmpl` (`"{{.NATSClientPort}}:4222"`; the host-side port is the `Params.NATSClientPort` tunable). `8222` is the NATS-standard monitoring port and its host-side mapping ships commented out in the template, so it is not exposed to the host by default.
- **`nats://nats:4222` (`DefaultNATSURLValue` in `internal/config/state.go`)**: compiled-in default consumed by the `worker start --nats-url` flag in `worker_start.go`, already overridable via `SYNTHORG_NATS_URL`.

## See also

- [cli-config-subcommands.md](cli-config-subcommands.md): the `synthorg config get / set / unset` interface and the full settable-keys inventory.
- [cli-persistence-backends.md](cli-persistence-backends.md): SQLite vs Postgres orchestration.
- [environment-variables.md](environment-variables.md): the backend's `SYNTHORG_*` env var registry (init-time, registry, runtime-override categories).
