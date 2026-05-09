# CLI (Go Binary)

Go tooling requires the module root as cwd. Use `go -C cli` which changes directory internally without affecting the shell. **Never use a bare `cd cli`** in the Bash tool; it poisons the cwd for every subsequent Bash call in the session. A short-lived subshell `cd` (`bash -c "cd cli && <cmd>"` or `(cd cli && <cmd>)`) is acceptable and is the sanctioned escape hatch for external tools that lack a `-C` flag (see the Shell Usage section in the root `CLAUDE.md`). `golangci-lint` is installed as an **external** binary (not a Go `tool` directive) to keep `cli/go.mod` free of GPL-3.0 transitive deps; run `scripts/install_cli_tools.sh` once to install it locally (CI uses `golangci/golangci-lint-action` directly).

## Quick Commands

```bash
go -C cli build -o synthorg ./main.go                                  # build CLI
go -C cli test ./...                                                   # run tests (fuzz targets run seed corpus only without -fuzz flag)
go -C cli vet ./...                                                    # vet
bash -c "cd cli && golangci-lint run"                                  # lint (subshell cd; golangci-lint has no -C flag -- requires scripts/install_cli_tools.sh)
go -C cli test -fuzz=FuzzYamlStr -fuzztime=30s ./internal/compose/     # fuzz example
go -C cli test -run='^$' -bench=. -benchmem ./internal/compose/        # run benchmarks for one package (skip Test/Example/Fuzz seed; modern `for b.Loop()` pattern)
go -C cli test -run='^$' -bench=. -benchmem -count=10 ./...            # capture benchmark snapshot across all packages
bash scripts/check_cli_bench_regression.sh                             # in-CI A/B compare HEAD vs merge-base (also runs as `cli-bench` job)
```

## Performance Benchmarks

`*_bench_test.go` files (next to their `*_test.go` siblings under `cli/internal/<pkg>/`) use Go's standard `testing.B` framework with the modern `for b.Loop()` pattern (Go 1.24+). They are picked up by `go test -bench=.`. By default `go test` also runs `Test*`, `Example*`, and `Fuzz*` seed-corpus functions in addition to the benchmarks; pass `-run='^$'` (the convention `check_cli_bench_regression.sh` uses internally) when capturing a clean benchmark snapshot so only `Benchmark*` functions execute.

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

`SYNTHORG_*` env vars without a corresponding flag (settable via env or `config set`) cover four buckets:

- **Backend / channel overrides**: `SYNTHORG_LOG_LEVEL`, `SYNTHORG_BACKEND_PORT`, `SYNTHORG_WEB_PORT`, `SYNTHORG_CHANNEL`, `SYNTHORG_IMAGE_TAG`, `SYNTHORG_TELEMETRY_ENABLED`, `SYNTHORG_AUTO_*` (UPDATE_CLI / PULL / RESTART).
- **Image / registry overrides**: `SYNTHORG_REGISTRY_HOST`, `SYNTHORG_IMAGE_REPO_PREFIX`, `SYNTHORG_DHI_REGISTRY`, `SYNTHORG_POSTGRES_IMAGE_TAG`, `SYNTHORG_NATS_IMAGE_TAG`, `SYNTHORG_FINE_TUNE_IMAGE` (any of these disables verification for that invocation). `SYNTHORG_POSTGRES_IMAGE_TAG` and `SYNTHORG_NATS_IMAGE_TAG` default to `Default{Postgres,NATS}ImageTag` in `cli/internal/config/state.go`, with the matching multi-arch index digest stored as a sibling `Default{Postgres,NATS}ImageDigest` constant in the same file. Both are kept current by a single Renovate customManager (one regex match per dep, capturing tag + digest together) that watches the `// renovate:` annotation on the tag constant. `cli/internal/verify/dhi.go` derives its `dhiPinnedIndexDigests` map from these constants at init -- state.go is the single source of truth. Renovate's docker-compose manager is disabled on `docker/compose.yml`, so any PR bumping `Default{Postgres,NATS}Image{Tag,Digest}` MUST hand-mirror the matching `image:` line in `docker/compose.yml` in the same commit; `cli/internal/verify/compose_sync_test.go` enforces this and fails the build on drift.
- **Timeouts and retry tuning**: `SYNTHORG_BACKUP_*_TIMEOUT`, `SYNTHORG_HEALTH_CHECK_TIMEOUT`, `SYNTHORG_SELF_UPDATE_*_TIMEOUT`, `SYNTHORG_TUF_FETCH_TIMEOUT`, `SYNTHORG_ATTESTATION_HTTP_TIMEOUT`, `SYNTHORG_IMAGE_VERIFY_TIMEOUT` (default 120s, hard min 1s), `SYNTHORG_IMAGE_PULL_ATTEMPTS` (1..100, default 3), `SYNTHORG_IMAGE_PULL_RETRY_DELAY` (default 2s, exponential).
- **Byte caps and ports**: `SYNTHORG_MAX_API_RESPONSE_BYTES` (default 4MiB), `SYNTHORG_MAX_BINARY_BYTES` (256MiB), `SYNTHORG_MAX_ARCHIVE_ENTRY_BYTES` (128MiB), `SYNTHORG_NATS_URL` (single source of truth shared with the backend's `communication.nats_url` setting; env-only, not in `config set`), `SYNTHORG_DEFAULT_NATS_STREAM_PREFIX`, `SYNTHORG_FINE_TUNE_HEALTH_PORT` (env-only, not in `config set`).

See [docs/reference/cli-env-vars.md](../docs/reference/cli-env-vars.md) for the full table with descriptions, byte-cap rationales, and the audit rationale for hardcoded `localhost` / `postgres:5432` / `nats:4222` literals (correct by design).

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

`synthorg config <subcommand>` exposes `show` / `get <key>` / `set <key> <value>` / `unset <key>` / `list` / `path` / `edit`. There are 37 settable keys (e.g. `backend_port`, `web_port`, `sandbox`, `image_tag`, `log_level`, `fine_tuning`, `telemetry_opt_in`, `channel`, plus all the tunables listed above). Compose-affecting keys trigger automatic `compose.yml` regeneration; toggling `fine_tuning` on requires `sandbox=true` and amd64.

Overriding any of `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, or `nats_image_tag` disables image signature + SLSA verification **for that invocation only** and writes a stderr warning on every invocation (not suppressed by `--quiet` or `--json`).

See [docs/reference/cli-config-subcommands.md](../docs/reference/cli-config-subcommands.md) for the full settable-key inventory, the compose-affecting subset, the verification-disabling override semantics, the tunable value formats (durations, byte sizes, integers, registry hosts, image tags, NATS URL grammar), and the `changelog_view` upgrade-walk recipe.

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
| `backup list` | `--limit`/`-n`, `--sort` (`newest`\|`oldest`\|`size`) |
| `backup restore` | `--confirm` (required), `--dry-run`, `--no-restart`, `--timeout` |
| `completion` | `[bash \| zsh \| fish \| powershell]`: emit shell autocompletion script (Cobra built-in) |
| `completion-install` | `[bash \| zsh \| fish \| powershell]`: write the autocompletion script into your shell startup (`~/.bashrc`, `~/.zshrc`, etc.) |
| `worker start` | `--workers` (int, default 4), `--nats-url` (precedence: flag > `SYNTHORG_NATS_URL` env > compiled default), `--stream-prefix`, `--container` (flag default `""`; falls back to `synthorg-backend` when unset): runs the distributed task-queue worker pool |
| `new <kind> <domain>` | `--dry-run`, `--overwrite`: scaffolds a conventions-clean Python file set under `src/synthorg/` for a new feature. `<kind>` is one of `service` / `persistence` / `tool` / `controller`. See [docs/reference/scaffolding.md](../docs/reference/scaffolding.md). |
| `wipe` | `--dry-run`, `--no-backup`, `--keep-images` |
| `doctor` | `--checks`, `--fix` |
| `version` | `--short` |
| `uninstall` | `--keep-data`, `--keep-images` |

## Persistence Backends

`--persistence-backend sqlite` (default, single-node) uses the in-process SQLite store under volume `synthorg-data`. `--persistence-backend postgres` adds a `dhi.io/postgres` DHI service (tag pinned via `DefaultPostgresImageTag` in `cli/internal/config/state.go`, kept current by Renovate) on port `3002` (override via `--postgres-port`) backed by volume `synthorg-pgdata`. Interactive `init` defaults to Postgres + NATS; non-interactive defaults to SQLite + internal bus.

Every generated `compose.yml` includes a one-shot `data-init` helper that chowns each named volume to its non-root owner (`65532:65532` for backend / NATS, `70:70` with mode `0700` for Postgres) before stateful services start. The Postgres / NATS services declare `depends_on: data-init: condition: service_completed_successfully`.

Backend auto-wire precedence: when both `SYNTHORG_DATABASE_URL` and `SYNTHORG_DB_PATH` are present, `SYNTHORG_DATABASE_URL` wins (Postgres is initialised; the SQLite path is ignored). A malformed URL raises loudly at startup rather than silently falling back to a no-persistence install. Atlas migrations run on every backend connection; the Atlas binary is baked into the backend image at `/usr/local/bin/atlas` from `arigaio/atlas:latest-community-distroless`, pinned by multi-arch manifest digest.

Port layout: `3000` web / `3001` backend / `3002` postgres / `3003` NATS client. `generate.go` validates port collisions across all enabled services.

`synthorg status` renders a verdict banner (`OK` / `DEGRADED` / `CRITICAL`) computed by `computeVerdict()` in `cli/cmd/status.go`. `CRITICAL` wins over `DEGRADED`; signals are gated on install expectations so an internal-bus install is not flagged `DEGRADED` merely because the health response omits `message_bus`.

See [docs/reference/cli-persistence-backends.md](../docs/reference/cli-persistence-backends.md) for the per-step Postgres orchestration (random-password generation, `SYNTHORG_POSTGRES_SSL_MODE` defaults, depends_on health gate), DHI cosign + SLSA verification cache (`verified_digests`), the NATS config file shape (`max_payload: 16MB`), and the verdict-banner escalation rules.
