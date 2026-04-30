---
title: CLI Environment Variables
description: Full SYNTHORG_* env var inventory honoured by the SynthOrg Go CLI binary, including timeouts, retry counts, image overrides, and the hardcoded-network-literal audit rationale.
---

# CLI Environment Variables

On-demand reference. The short list in `cli/CLAUDE.md` is the env-var-only settings (no corresponding flag). This page is the full inventory plus the audit rationale for hardcoded network literals.

## Env-var-only settings

| Env Var | Description |
|---------|-------------|
| `SYNTHORG_LOG_LEVEL` | Override backend log level |
| `SYNTHORG_BACKEND_PORT` | Override backend API port |
| `SYNTHORG_WEB_PORT` | Override web dashboard port |
| `SYNTHORG_CHANNEL` | Override release channel (stable / dev) |
| `SYNTHORG_IMAGE_TAG` | Override container image tag |
| `SYNTHORG_AUTO_UPDATE_CLI` | Auto-accept CLI self-updates |
| `SYNTHORG_AUTO_PULL` | Auto-accept container image pulls |
| `SYNTHORG_AUTO_RESTART` | Auto-restart containers after update |
| `SYNTHORG_TELEMETRY_ENABLED` | Enable anonymous project telemetry (true / false) |
| `SYNTHORG_FINE_TUNE_IMAGE` | Fine-tune container image ref read by the backend. Set by the CLI in the generated compose.yml to the variant-specific verified image (`synthorg-fine-tune-gpu` or `synthorg-fine-tune-cpu`), chosen via `synthorg init` and persisted as `fine_tuning_variant` in config.json. Not read by the CLI; manual operator overrides bypass CLI signature / provenance verification and are not supported. |
| `SYNTHORG_REGISTRY_HOST` | Override default container registry hostname (disables verification when set) |
| `SYNTHORG_IMAGE_REPO_PREFIX` | Override default image repository prefix (disables verification when set) |
| `SYNTHORG_DHI_REGISTRY` | Override Docker Hardened Images registry (disables verification when set) |
| `SYNTHORG_POSTGRES_IMAGE_TAG` | Override pinned Postgres DHI tag (disables verification when set) |
| `SYNTHORG_NATS_IMAGE_TAG` | Override pinned NATS DHI tag (disables verification when set) |
| `SYNTHORG_DEFAULT_NATS_URL` | Override `synthorg worker start --nats-url` default |
| `SYNTHORG_DEFAULT_NATS_STREAM_PREFIX` | Override `synthorg worker start --stream-prefix` default |
| `SYNTHORG_BACKUP_CREATE_TIMEOUT` | Override `synthorg backup create --timeout` default (duration, e.g. `60s`) |
| `SYNTHORG_BACKUP_RESTORE_TIMEOUT` | Override `synthorg backup restore --timeout` default |
| `SYNTHORG_HEALTH_CHECK_TIMEOUT` | HTTP timeout for health endpoint probes (duration) |
| `SYNTHORG_SELF_UPDATE_HTTP_TIMEOUT` | HTTP timeout for CLI binary download (duration) |
| `SYNTHORG_SELF_UPDATE_API_TIMEOUT` | HTTP timeout for GitHub API metadata fetches (duration) |
| `SYNTHORG_TUF_FETCH_TIMEOUT` | HTTP timeout for Sigstore TUF trusted root fetch (duration) |
| `SYNTHORG_ATTESTATION_HTTP_TIMEOUT` | HTTP timeout for GitHub attestation API (duration) |
| `SYNTHORG_MAX_API_RESPONSE_BYTES` | Maximum bytes for API / checksum downloads (default `4MiB`; accepts `1MiB`, `1048576`). Sized for the list-commits walk used by `synthorg update`: each commit object inlines the full PGP signature plus signed-payload duplicate plus 20+ author / committer URL fields (~15 KiB / commit), so a typical 25-entry page is ~400 KiB and 4 MiB gives 10x headroom. Hard ceiling is 1 GiB via `MaxBytesCeiling`. |
| `SYNTHORG_MAX_BINARY_BYTES` | Maximum bytes for CLI binary archive downloads (accepts `256MiB`) |
| `SYNTHORG_MAX_ARCHIVE_ENTRY_BYTES` | Maximum bytes per archive entry during extraction (accepts `128MiB`) |
| `SYNTHORG_IMAGE_VERIFY_TIMEOUT` | Context timeout for the cosign + SLSA verification pass during `start` and `update`. Duration, default `120s`, hard minimum `1s` (shorter values would bypass verification by silently timing out before cosign / SLSA / TUF completes network I/O). |
| `SYNTHORG_IMAGE_PULL_ATTEMPTS` | Retry count for transient `docker pull` failures on standalone images (integer in `[1, 100]`, default `3`) |
| `SYNTHORG_IMAGE_PULL_RETRY_DELAY` | Base backoff between pull retries. Exponential: N-th retry waits `delay * 2^(N-1)` seconds (e.g. `2s` base produces 2s, 4s, 8s, 16s, ...), saturated at a 5 min ceiling to guard against overflow when `image_pull_attempts` is large. Duration, default `2s`. |
| `SYNTHORG_FINE_TUNE_HEALTH_PORT` | Override fine-tune container health server port (integer in `[1, 65535]`, default `15002`). Env-only: read directly by the fine-tune Python runner, so it is **not** exposed as a `synthorg config set` key and does not trigger compose regeneration. |

## Hardcoded network literals (audit rationale)

The CLI contains several `localhost` / service-DNS / port literals that look non-configurable but are correct by design:

- **`localhost` in `doctor.go` / `start.go` / `status.go` / `wipe.go` / `update.go`**: these print URLs pointing at the operator's own host (e.g. `http://localhost:<BackendPort>/api/v1/readyz`). The port is flag / env-driven (`SYNTHORG_BACKEND_PORT`, `SYNTHORG_WEB_PORT`); the hostname is literally the host the CLI is running on.
- **`postgres:5432` in `compose/generate.go::pgDSN`**: docker-compose internal DNS, container-to-container. The host-side Postgres port is a separate `Params.PostgresPort` tunable rendered in `compose.yml.tmpl`.
- **`nats:4222` / `nats:8222` in `compose.yml.tmpl`**: NATS client and HTTP monitoring ports inside the compose network. `nats` is the compose service name. `8222` is the NATS-standard monitoring port, not exposed to the host.
- **`nats://nats:4222` in `worker_start.go`**: compiled-in default for the `--nats-url` flag, already overridable via `SYNTHORG_DEFAULT_NATS_URL`.

## See also

- [cli-config-subcommands.md](cli-config-subcommands.md): the `synthorg config get / set / unset` interface and the full settable-keys inventory.
- [cli-persistence-backends.md](cli-persistence-backends.md): SQLite vs Postgres orchestration.
- [environment-variables.md](environment-variables.md): the backend's `SYNTHORG_*` env var registry (init-time, registry, runtime-override categories).
