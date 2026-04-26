# CLI (Go Binary)

Go tooling requires the module root as cwd. Use `go -C cli` which changes directory internally without affecting the shell. **Never use a bare `cd cli`** in the Bash tool; it poisons the cwd for every subsequent Bash call in the session. A short-lived subshell `cd` (`bash -c "cd cli && <cmd>"` or `(cd cli && <cmd>)`) is acceptable and is the sanctioned escape hatch for external tools that lack a `-C` flag (see the Shell Usage section in the root `CLAUDE.md`). `golangci-lint` is installed as an **external** binary (not a Go `tool` directive) to keep `cli/go.mod` free of GPL-3.0 transitive deps; run `scripts/install_cli_tools.sh` once to install it locally (CI uses `golangci/golangci-lint-action` directly).

## Quick Commands

```bash
go -C cli build -o synthorg ./main.go                                  # build CLI
go -C cli test ./...                                                   # run tests (fuzz targets run seed corpus only without -fuzz flag)
go -C cli vet ./...                                                    # vet
bash -c "cd cli && golangci-lint run"                                  # lint (subshell cd; golangci-lint has no -C flag -- requires scripts/install_cli_tools.sh)
go -C cli test -fuzz=FuzzYamlStr -fuzztime=30s ./internal/compose/     # fuzz example
go -C cli test -bench=. -benchmem ./internal/compose/                  # run benchmarks for one package (modern `for b.Loop()` pattern)
go -C cli test -bench=. -benchmem -count=10 ./...                      # capture benchmark snapshot across all packages
bash scripts/check_cli_bench_regression.sh                             # in-CI A/B compare HEAD vs merge-base (also runs as `cli-bench` job)
```

## Performance Benchmarks

`*_bench_test.go` files (next to their `*_test.go` siblings under `cli/internal/<pkg>/`) use Go's standard `testing.B` framework with the modern `for b.Loop()` pattern (Go 1.24+). They are picked up by `go test -bench=.` and excluded from regular `go test ./...` runs (which only run `Test*` functions).

Regression detection uses an **in-CI A/B compare** (`scripts/check_cli_bench_regression.sh`): the script captures benches at PR HEAD, checks out the merge-base against `origin/main`, captures again on the same runner, and runs `benchstat` to detect deltas above a threshold (default 15% slowdown fails the job). No committed baseline file -- the comparison runs entirely within one CI job on the same hardware, sidestepping cross-architecture variance entirely. The `cli-bench` job in `.github/workflows/cli.yml` only runs on `pull_request` events (it needs the merge-base).

## Local Setup

Install the external lint toolchain once per development machine:

```bash
bash scripts/install_cli_tools.sh
```

This installs the pinned `golangci-lint` version that matches CI (`.github/workflows/cli.yml`). Re-run after bumping the version. The pre-commit and pre-push hooks assume `golangci-lint` is on `PATH` (in pre-commit.ci it is skipped because the hosted runner does not have Go installed).

## Package Structure

```text
cli/
  cmd/            # Cobra commands (init, start, stop, status, logs, doctor, update, cleanup, wipe, config, etc.), global options, exit codes, env var constants
  internal/       # version, config, docker, compose, health, diagnostics, images, selfupdate, completion, ui, verify
```

## Global Flags

All commands accept these persistent flags (precedence: flag > env var > config > default):

| Flag | Short | Env Var | Description |
|------|-------|---------|-------------|
| `--data-dir` | | `SYNTHORG_DATA_DIR` | Data directory (default: platform-appropriate) |
| `--skip-verify` | | `SYNTHORG_NO_VERIFY` / `SYNTHORG_SKIP_VERIFY` | Skip image signature verification |
| `--quiet` | `-q` | `SYNTHORG_QUIET` | Errors only, no spinners/hints/boxes |
| `--verbose` | `-v` | | Increase verbosity (`-v`=verbose, `-vv`=trace) |
| `--no-color` | | `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb` | Disable ANSI color output |
| `--plain` | | | ASCII-only output (no Unicode, no spinners) |
| `--json` | | | Machine-readable JSON output |
| `--yes` | `-y` | `SYNTHORG_YES` | Auto-accept all prompts (non-interactive) |
| `--help-all` | | | Show help for all commands (recursive) |

Config-driven overrides (set via `synthorg config set`): `color never` implies `--no-color`, `color always` forces color on non-TTYs, `output json` implies `--json`, `hints` mode is config-only (always/auto/never).

## Hint Tiers

The CLI uses four hint tiers with different visibility rules per `hints` mode. When adding hints, choose the tier that matches the intent:

| Tier | `always` | `auto` | `never` | `--quiet` | Use for |
|------|----------|--------|---------|-----------|---------|
| `HintError` | shown | shown | shown | suppressed | Error recovery (always visible unless quiet) |
| `HintNextStep` | shown | shown | shown | suppressed | Natural next action, destructive-action feedback |
| `HintTip` | shown | once/session | suppressed | suppressed | Config automation suggestions (e.g. `auto_pull`) |
| `HintGuidance` | shown | suppressed | suppressed | suppressed | Flag/feature discovery (e.g. `--watch`, `--keep N`) |

`HintTip` deduplicates within a session (same message shown at most once). `HintGuidance` is invisible in the default `auto` mode; only users who opt in with `synthorg config set hints always` see it.

## Additional Env Vars

No corresponding flag, settable via env var or `config set`:

| Env Var | Description |
|---------|-------------|
| `SYNTHORG_LOG_LEVEL` | Override backend log level |
| `SYNTHORG_BACKEND_PORT` | Override backend API port |
| `SYNTHORG_WEB_PORT` | Override web dashboard port |
| `SYNTHORG_CHANNEL` | Override release channel (stable/dev) |
| `SYNTHORG_IMAGE_TAG` | Override container image tag |
| `SYNTHORG_AUTO_UPDATE_CLI` | Auto-accept CLI self-updates |
| `SYNTHORG_AUTO_PULL` | Auto-accept container image pulls |
| `SYNTHORG_AUTO_RESTART` | Auto-restart containers after update |
| `SYNTHORG_TELEMETRY` | Enable anonymous project telemetry (true/false) |
| `SYNTHORG_FINE_TUNE_IMAGE` | Fine-tune container image ref read by the backend. Set by the CLI in the generated compose.yml to the variant-specific verified image (`synthorg-fine-tune-gpu` or `synthorg-fine-tune-cpu`), chosen via `synthorg init` and persisted as `fine_tuning_variant` in config.json. Not read by the CLI; manual operator overrides bypass CLI signature/provenance verification and are not supported. |
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
| `SYNTHORG_MAX_API_RESPONSE_BYTES` | Maximum bytes for API/checksum downloads (accepts `1MiB`, `1048576`) |
| `SYNTHORG_MAX_BINARY_BYTES` | Maximum bytes for CLI binary archive downloads (accepts `256MiB`) |
| `SYNTHORG_MAX_ARCHIVE_ENTRY_BYTES` | Maximum bytes per archive entry during extraction (accepts `128MiB`) |
| `SYNTHORG_IMAGE_VERIFY_TIMEOUT` | Context timeout for the cosign + SLSA verification pass during `start` and `update`. Duration, default `120s`, hard minimum `1s` (shorter values would bypass verification by silently timing out before cosign/SLSA/TUF completes network I/O) |
| `SYNTHORG_IMAGE_PULL_ATTEMPTS` | Retry count for transient `docker pull` failures on standalone images (integer in `[1, 100]`, default `3`) |
| `SYNTHORG_IMAGE_PULL_RETRY_DELAY` | Base backoff between pull retries. Exponential: N-th retry waits `delay * 2^(N-1)` seconds (e.g. `2s` base → 2s, 4s, 8s, 16s, ...), saturated at a 5 min ceiling to guard against overflow when `image_pull_attempts` is large. Duration, default `2s` |
| `SYNTHORG_FINE_TUNE_HEALTH_PORT` | Override fine-tune container health server port (integer in `[1, 65535]`, default `15002`). Env-only: read directly by the fine-tune Python runner, so it is **not** exposed as a `synthorg config set` key and does not trigger compose regeneration. |

### Hardcoded network literals (audit rationale)

The CLI contains several `localhost` / service-DNS / port literals that look non-configurable but are correct by design:

- **`localhost` in `doctor.go` / `start.go` / `status.go` / `wipe.go` / `update.go`**: these print URLs pointing at the operator's own host (e.g. `http://localhost:<BackendPort>/api/v1/readyz`). The port is flag/env-driven (`SYNTHORG_BACKEND_PORT`, `SYNTHORG_WEB_PORT`); the hostname is literally the host the CLI is running on.
- **`postgres:5432` in `compose/generate.go::pgDSN`**: docker-compose internal DNS, container-to-container. The host-side Postgres port is a separate `Params.PostgresPort` tunable rendered in `compose.yml.tmpl`.
- **`nats:4222` / `nats:8222` in `compose.yml.tmpl`**: NATS client and HTTP monitoring ports inside the compose network. `nats` is the compose service name. `8222` is the NATS-standard monitoring port, not exposed to the host.
- **`nats://nats:4222` in `worker_start.go`**: compiled-in default for the `--nats-url` flag, already overridable via `SYNTHORG_DEFAULT_NATS_URL` (see above).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error |
| 2 | Usage error (bad arguments) |
| 3 | Unhealthy (backend/containers) |
| 4 | Unreachable (Docker not available) |
| 10 | Updates available (`--check`) |

## Config Subcommands

`synthorg config <subcommand>`:

| Subcommand | Description |
|------------|-------------|
| `show` | Display all current settings (default when no subcommand) |
| `get <key>` | Get a single config value (37 gettable keys) |
| `set <key> <value>` | Set a config value (37 settable keys, compose-affecting keys trigger regeneration) |
| `unset <key>` | Reset a key to its default value |
| `list` | Show all keys with resolved value and source (env/config/default) |
| `path` | Print the config file path |
| `edit` | Open config file in $VISUAL/$EDITOR |

Settable keys: `auto_apply_compose`, `auto_cleanup`, `auto_pull`, `auto_restart`, `auto_start_after_wipe`, `auto_update_cli`, `backend_port`, `changelog_view`, `channel`, `color`, `docker_sock`, `fine_tuning`, `fine_tuning_variant`, `hints`, `image_tag`, `log_level`, `output`, `sandbox`, `telemetry_opt_in`, `timestamps`, `web_port`, plus the tunables: `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, `nats_image_tag`, `default_nats_url`, `default_nats_stream_prefix`, `backup_create_timeout`, `backup_restore_timeout`, `health_check_timeout`, `self_update_http_timeout`, `self_update_api_timeout`, `tuf_fetch_timeout`, `attestation_http_timeout`, `image_verify_timeout`, `image_pull_attempts`, `image_pull_retry_delay`, `max_api_response_bytes`, `max_binary_bytes`, `max_archive_entry_bytes`. Keys that affect Docker compose (`backend_port`, `web_port`, `sandbox`, `docker_sock`, `image_tag`, `log_level`, `telemetry_opt_in`, `fine_tuning`, `fine_tuning_variant`, `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, `nats_image_tag`, `default_nats_url`, `default_nats_stream_prefix`) trigger automatic `compose.yml` regeneration. Toggling `fine_tuning` on requires `sandbox=true` and amd64; validation runs at `config set` time so inconsistent combinations fail before the next `start`.

Overriding any of `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, or `nats_image_tag` transfers trust to the operator: the CLI disables image signature and SLSA provenance verification **for that invocation only** and writes a one-shot warning to stderr on **every** invocation where the override is active. The warning is **not** suppressed under `--quiet` or `--json`; a safety-critical notice must appear in the audit trail of every scripted run. The pinned SAN regex and DHI digest map are bound to the default values, so verification cannot succeed against a custom deployment target.

### Tunable value formats

- **Durations**: Go `time.ParseDuration` format. Examples: `30s`, `5m`, `1h`, `500ms`. Values must be strictly positive.
- **Byte sizes**: plain integers (`1048576` = 1 MiB) or suffixed values. IEC binary suffixes: `B`, `KiB`, `MiB`, `GiB` (powers of 1024). SI decimal suffixes: `KB`, `MB`, `GB` (powers of 1000). Case-insensitive. Rejected: negative, zero, or values exceeding the 1 GiB runtime ceiling.
- **Integers**: plain decimal integers. Each integer tunable declares its own `[min, max]` range (e.g. `image_pull_attempts` is `[1, 100]`). Rejected: non-numeric values, negatives, or values outside the per-tunable range.
- **Registry hosts**: DNS hostname, optionally with `:port`. Matches `[a-zA-Z0-9][a-zA-Z0-9.-]*(:[0-9]+)?`.
- **Image tags**: Docker tag grammar. Matches `[a-zA-Z0-9][a-zA-Z0-9._-]*`.
- **NATS URLs**: must use `nats://`, `tls://`, or `nats+tls://` scheme and include a host.
- **NATS stream prefix**: uppercase alphanumerics with `_` or `-`. Matches `[A-Z0-9][A-Z0-9_-]*`.
- **`changelog_view`**: enum, either `highlights` (default) or `commits`. Sets the default view for the `synthorg update` upgrade walk between installed and target releases. `highlights` shows the AI-generated three-section summary; `commits` shows the Release Please commit-based changelog. Inside the walk, `c` toggles between the two views for the current session without modifying the persisted value. On the `dev` channel the setting is moot: dev pre-releases have no Highlights block, so the walk always renders a single combined commit list fetched via the GitHub compare API. When the walk cannot render (network failure, the installed dev pre-release tag was pruned from the remote, or the range is empty) the CLI prints an explicit `Warn` line explaining the cause and falls back to the terse offline notice; it never silently degrades.

## Per-Command Flags

| Command | Flags |
|---------|-------|
| `init` | `--backend-port`, `--web-port`, `--sandbox`, `--log-level` (required for non-interactive mode); optional: `--image-tag`, `--channel`, `--bus-backend`, `--persistence-backend`, `--postgres-port`, `--encrypt-secrets` ("true" or "false", default "true"; encrypts connection secrets at rest via Fernet) |
| `start` | `--no-wait`, `--timeout`, `--no-pull`, `--dry-run`, `--no-detach`, `--no-verify` |
| `stop` | `--timeout`/`-t`, `--volumes` |
| `status` | `--watch`/`-w`, `--interval`, `--wide`, `--no-trunc`, `--services`, `--check` |
| `logs` | `--follow`/`-f`, `--tail`, `--since`, `--until`, `--timestamps`/`-t`, `--no-log-prefix` |
| `update` | `--dry-run`, `--no-restart`, `--timeout`, `--cli-only`, `--images-only`, `--check` |
| `cleanup` | `--dry-run`, `--all`, `--keep N` |
| `backup create` | `--output`/`-o`, `--timeout` |
| `backup list` | `--limit`/`-n`, `--sort` |
| `backup restore` | `--confirm` (required), `--dry-run`, `--no-restart`, `--timeout` |
| `completion` | `[bash \| zsh \| fish \| powershell]`: emit shell autocompletion script (Cobra built-in) |
| `completion-install` | `[bash \| zsh \| fish \| powershell]`: write the autocompletion script into your shell startup (`~/.bashrc`, `~/.zshrc`, etc.) |
| `worker start` | `--workers` (int, default 4), `--nats-url`, `--stream-prefix`, `--container` (flag default `""`; falls back to `synthorg-backend` when unset): runs the distributed task-queue worker pool |
| `wipe` | `--dry-run`, `--no-backup`, `--keep-images` |
| `doctor` | `--checks`, `--fix` |
| `version` | `--short` |
| `uninstall` | `--keep-data`, `--keep-images` |

## Persistence Backends

The CLI orchestrates two persistence backends:

| Backend | Flag | Port | Data volume | When to use |
|---------|------|------|-------------|-------------|
| `sqlite` (default) | `--persistence-backend sqlite` | n/a (in-process) | `synthorg-data` | Single-node, development, small deployments |
| `postgres` | `--persistence-backend postgres` | `3002` (default, override with `--postgres-port`) | `synthorg-pgdata` | Multi-instance, production, high concurrency |

### Volume ownership (`data-init`)

Every generated `compose.yml` includes a `data-init` helper container (busybox) that runs once before the stateful services start. Its job is to chown each named volume to the UID of the non-root user that will own it:

- `synthorg-data` -> `65532:65532` (backend / distroless nonroot)
- `synthorg-pgdata` -> `70:70` with mode `0700` (DHI postgres user; `initdb` requires exclusive 0700 or it aborts with "permissions should be u=rwx (0700) or u=rwx,g=rx (0750)"); only mounted when `--persistence-backend postgres`
- `synthorg-nats-data` -> `65532:65532` (DHI nats `nonroot` user); only mounted when `--bus-backend nats`

Fresh Docker named volumes are owned by `root:root` at creation, and DHI images run as non-root with no capability to self-chown, so this one-shot container is required for every backend selection to avoid permission errors. The `postgres` and `nats` services both declare `depends_on: data-init: condition: service_completed_successfully` to block on the chown before starting.

### Postgres orchestration

When `--persistence-backend postgres` is selected, `synthorg init`:

1. Adds a `dhi.io/postgres:18-debian13` DHI (Docker Hardened Image) service to the generated `compose.yml` (read-only rootfs, minimal capabilities via `cap_add`, `pg_isready` healthcheck, named volume `synthorg-pgdata`).
2. Extends the `data-init` helper (see above) to also chown `synthorg-pgdata` to `70:70` with mode `0700`.
3. Generates a 32-byte URL-safe random password via `crypto/rand` and persists it to `config.json` (`postgres_password`). Re-init preserves the existing password to avoid breaking the running container.
4. Wires `SYNTHORG_DATABASE_URL=postgresql://synthorg:<password>@postgres:5432/synthorg` into the backend container's environment. The SQLite-only `SYNTHORG_DB_PATH` variable is omitted.
5. Sets `SYNTHORG_POSTGRES_SSL_MODE=disable` on the backend because the local DHI postgres inside the docker bridge runs plaintext. Override to `verify-full` for production deployments where TLS terminates at Postgres with trusted certs.
6. Declares `depends_on: postgres: condition: service_healthy` on the backend service so backend startup blocks until Postgres accepts connections.

Backend auto-wire precedence (`src/synthorg/api/app.py`): when both `SYNTHORG_DATABASE_URL` and `SYNTHORG_DB_PATH` are present, `SYNTHORG_DATABASE_URL` wins and Postgres is initialized; the SQLite path is ignored. A malformed URL raises loudly at startup rather than silently falling back to a no-persistence install.

**Interactive mode (TUI)** defaults to PostgreSQL + NATS; **non-interactive mode** defaults to SQLite + internal bus. Use `--persistence-backend sqlite` / `--bus-backend internal` in flags to override.

`synthorg start` brings up Postgres first (via compose ordering), then the backend applies Atlas migrations on connection. The Atlas CLI binary is sourced at image-build time from the upstream `arigaio/atlas:latest-community-distroless` image, pinned by multi-arch manifest digest in `docker/backend/Dockerfile`. Renovate's built-in `docker` manager tracks the digest automatically; rebuilds that pick up Go stdlib security patches flow through normal Renovate PRs with no manual SHA refresh. The static binary is copied into the distroless runtime at `/usr/local/bin/atlas` so `persistence.migrate()` can shell out without needing a package manager. `synthorg stop` preserves `synthorg-pgdata` unless `--volumes` is passed. `synthorg status --wide` reports Postgres container health plus the `synthorg-pgdata` volume size.

DHI images are verified before pulling via cosign ECDSA signature + SLSA v1 provenance attestation + Rekor transparency log. Verification results are cached in `config.json` (`verified_digests`) and invalidated when Renovate bumps the pinned index digest.

Port layout: `3000` web / `3001` backend / `3002` postgres / `3003` NATS client. `generate.go` validates port collisions: web vs backend always; postgres vs web/backend/NATS when postgres enabled; NATS vs web/backend when distributed bus mode is active.

### NATS configuration file

When `--bus-backend nats` is selected, `synthorg init` writes `nats.conf` next to the generated `compose.yml` and the NATS service bind-mounts it at `/etc/nats/nats.conf` (read-only). The canonical config content lives in `cli/internal/compose/nats_config.go` (`NATSConfigContent`) and currently sets `max_payload: 16MB`, sized for full LLM agent outputs and meeting transcripts while staying well under NATS's 64MB ceiling. The helper `writeNATSConfigIfNeeded` keeps the file in sync on every compose write (init, start's digest pin rewrite, `config set`, update's compose refresh) and removes a stale `nats.conf` when switching back to the internal bus.

### Status banner verdict levels

`synthorg status` renders a top-of-screen verdict banner computed by `computeVerdict()` in `cli/cmd/status.go`:

- `OK`: collapses to a single green "All systems operational" line; the happy path stays compact.
- `DEGRADED`: amber box listing recoverable issues (e.g., a service restarting, or distributed bus expected but not wired).
- `CRITICAL`: red box for unrecoverable state (e.g., backend unreachable, persistence not wired when expected, any container unhealthy).

Escalation rules: `CRITICAL` wins over `DEGRADED`, and signals are gated on install expectations: a default internal-bus install is not flagged `DEGRADED` merely because the backend's health response omits `message_bus` (only `--bus-backend nats` installs expect one). An unmatched `--services` filter reports `OK`, not `CRITICAL`, because `renderContainersSection` already explains "No containers match requested services".
