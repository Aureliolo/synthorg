# CLI (Go Binary)

Go tooling needs the module root as cwd: use `go -C cli` (changes dir internally, no shell effect). **Never a bare `cd cli`** in the Bash tool (poisons the cwd for the session); a short-lived subshell (`bash -c "cd cli && <cmd>"`) is the sanctioned escape hatch for tools lacking a `-C` flag. `golangci-lint` is an **external** binary (not a Go `tool` directive) to keep `cli/go.mod` free of GPL-3.0 transitive deps; `bash scripts/install_cli_tools.sh` once per machine to install it (CI uses `golangci/golangci-lint-action`).

## Quick Commands

```bash
go -C cli build -o synthorg ./main.go                                  # build CLI
go -C cli test ./...                                                   # tests (fuzz targets run seed corpus only without -fuzz)
go -C cli vet ./...                                                    # vet
bash -c "cd cli && golangci-lint run"                                  # lint (subshell cd; golangci-lint has no -C flag)
go -C cli test -fuzz=FuzzYamlStr -fuzztime=30s ./internal/compose/     # fuzz example
go -C cli test -run='^$' -bench=. -benchmem ./internal/compose/        # benchmarks for one package
bash scripts/check_cli_bench_regression.sh                             # in-CI A/B compare HEAD vs merge-base (cli-bench job)
```

- Benchmarks: `*_bench_test.go` siblings use `testing.B` + `for b.Loop()` (Go 1.24+); pass `-run='^$'` for a clean snapshot. Regression detection is an in-CI A/B compare on one runner (no committed baseline), PR-events only.

## Package Structure

```text
cli/
  cmd/            # Cobra commands (init/start/stop/status/logs/doctor/update/cleanup/wipe/config/worker/new...), global options, exit codes, env constants
  internal/       # version, config, docker, compose, health, diagnostics, images, selfupdate, completion, ui, verify, backup, scaffold
```

## Global Flags

Persistent on all commands (precedence: flag > env > config > default):

| Flag | Short | Env Var | Description |
|------|-------|---------|-------------|
| `--data-dir` | | `SYNTHORG_DATA_DIR` | Data directory (default: platform-appropriate) |
| `--skip-verify` | | `SYNTHORG_SKIP_VERIFY` | Skip image signature verification |
| `--quiet` | `-q` | `SYNTHORG_QUIET` | Errors only |
| `--verbose` | `-v` | | `-v`=verbose, `-vv`=trace |
| `--no-color` | | `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb` | Disable ANSI colour |
| `--plain` | | | ASCII-only output |
| `--json` | | | Machine-readable JSON |
| `--yes` | `-y` | `SYNTHORG_YES` | Auto-accept prompts |
| `--help-all` | | | Recursive help |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error |
| 2 | Usage error (bad arguments) |
| 3 | Unhealthy (backend/containers) |
| 4 | Unreachable (Docker not available) |
| 10 | Updates available (`--check`) |

## Hint Tiers

Four tiers gated by `hints` mode; pick by intent when adding hints:

| Tier | `always` | `auto` | `never` | `--quiet` | Use for |
|------|----------|--------|---------|-----------|---------|
| `HintError` | shown | shown | shown | suppressed | Error recovery |
| `HintNextStep` | shown | shown | shown | suppressed | Natural next action |
| `HintTip` | shown | once/session | suppressed | suppressed | Config automation suggestions |
| `HintGuidance` | shown | suppressed | suppressed | suppressed | Flag/feature discovery |

## Per-Command Flags

| Command | Flags |
|---------|-------|
| `init` | `--backend-port`, `--web-port`, `--sandbox`, `--log-level` (required non-interactive); optional `--image-tag`, `--channel`, `--bus-backend`, `--persistence-backend`, `--postgres-port`, `--encrypt-secrets` (default "true", Fernet at rest) |
| `start` | `--no-wait`, `--timeout`, `--no-pull`, `--dry-run`, `--no-detach`, `--no-verify` |
| `stop` | `--timeout`/`-t`, `--volumes` |
| `status` | `--watch`/`-w`, `--interval`, `--wide`, `--no-trunc`, `--services`, `--check` |
| `logs` | `--follow`/`-f`, `--tail`, `--since`, `--until`, `--timestamps`/`-t`, `--no-log-prefix` |
| `update` | `--dry-run`, `--no-restart`, `--timeout`, `--verify-timeout`, `--cli-only`, `--images-only`, `--check` |
| `cleanup` | `--dry-run`, `--all`, `--keep N` |
| `backup create/list/restore` | `-o`/`--timeout`; `--limit`/`--sort`; `--confirm` (required)/`--dry-run`/`--no-restart`/`--timeout` |
| `worker start` | `--workers` (default 4), `--nats-url`, `--stream-prefix`, `--container` |
| `new <kind> <domain>` | `--dry-run`, `--overwrite`; `<kind>` = service/persistence/tool/controller. See [scaffolding.md](../docs/reference/scaffolding.md). |
| `wipe` | `--dry-run`, `--no-backup`, `--keep-images` |
| `doctor` | `--checks`, `--fix` |
| `uninstall` | `--keep-data`, `--keep-images` |

## Notes (detail in reference docs)

- **Teardown contract**: `stop`/`wipe`/`uninstall` load via `config.LoadForTeardown` (never strict `Validate`), tolerate a missing compose.yml/Docker, never refuse on invalid config; the binary installs to a sibling tree, never inside the data dir, so teardown uses a plain `os.RemoveAll`. `stop` is in this set for the same reason as the others: the non-coercible backends keep failing a strict load by design, and refusing over one would leave containers running with no in-CLI way to stop them. Enforced by `teardown_load_test.go`. Add new teardown code to this best-effort pattern (`teardown_shared.go`).
- **Recovery contract**: an unrecognised enum value must never brick the commands that diagnose and repair an install. Every closed-set field in `state.go` is classified by blast radius: a *presentation* field (`channel`, `log_level`, `hints`, ...) gets a row in `enumFields` and `Coerce` (`internal/config/coerce.go`) substitutes the default before `Validate` runs, so `config.Load` succeeds; a field selecting **where data lives** (`persistence_backend`, `memory_backend`) gets an entry in `nonCoercibleEnums` with a stated reason and keeps failing `Load`, because defaulting it would point `start` at an empty database. Unclassified is not an option: `TestEveryAllowlistIsClassified` derives the allowlist set from `state.go` itself and fails on a new one. Coercions ride on `State.Coerced` (`json:"-"`, never persisted); root warns once per invocation and `doctor` reports them. Inspection commands (`doctor`, `config`) read through `loadForInspection`/`config.LoadTolerant`, which skips `Validate` so a config too broken to run is still diagnosable; `init`'s secret carry-forward uses `config.LoadForReinit` (skips `Validate` like teardown; a read/parse failure is still fatal). `writeInitFiles` validates before writing so `init` can never persist a config it cannot load back. **No error may tell an operator to delete `config.json` without naming `master_key`, `settings_key`, `cursor_secret` and `postgres_password` first** (`irreplaceableSecretsAdvice`): it is the only copy, and deleting it orphans every stored connection secret.
- **Backend health budget**: `--start-period` in `docker/backend/Dockerfile` is the ONLY definition; no compose layer declares a backend `healthcheck:` (one would replace the image's wholesale, start period included). It is derived from a measured cold boot, never guessed: re-measure with `scripts/measure_backend_boot.sh` before changing it, and CI fails a build whose boot eats past `budget-fraction` of it (`.github/actions/smoke-test-backend-image` parses the value rather than hardcoding a deadline). uvicorn runs the whole ASGI lifespan before binding, so the start period must cover the entire boot, not just interpreter start. The image ships compiled bytecode (`compileall -f`, after an un-swallowed `__pycache__` strip that discards build-context contamination) because the runtime cannot cache it: `PYTHONDONTWRITEBYTECODE=1` + `read_only`; builder and runtime must stay on one CPython minor or every `.pyc` is silently ignored. A crash loop restarting inside the start period never reports unhealthy, so `composeUpWithProgress` aborts on restart count rather than waiting on a dependency that will never resolve. Enforced by `tests/unit/test_backend_image_bytecode.py` + `cli/internal/compose/health_budget_test.go`.
- **Env vars + config keys**: 48 settable config keys via `synthorg config <show|get|set|import|unset|list|path|edit>` (set applies atomically); `SYNTHORG_*` env vars cover backend/channel, image/registry (overriding registry/image keys disables verification for that invocation + warns), timeout/retry tuning, and byte caps. `Default{Postgres,NATS}Image{Tag,Digest}` in `cli/internal/config/state.go` is the single source of truth (Renovate-managed; hand-mirror `docker/compose.yml`, enforced by `compose_sync_test.go`). Full tables: [cli-env-vars.md](../docs/reference/cli-env-vars.md), [cli-config-subcommands.md](../docs/reference/cli-config-subcommands.md).
- **Update self-check**: the CLI self-update check retries transient GitHub failures (5xx / network blip) with bounded exponential backoff inside the `self_update_api_timeout` budget. If the check still cannot complete (GitHub unreachable / rate-limited), `synthorg update` aborts with exit 1 rather than continuing blindly into the compose/image pull (images live on `ghcr.io`, so a real outage fails that too); use `synthorg update --images-only` to refresh container images without the CLI check.
- **Doctor**: `synthorg doctor [--checks name,...] [--fix]` collects diagnostics, saves a timestamped `0600` report under the data dir. Categories: environment, health, containers, images, compose, config, disk, errors.
- **Persistence backends**: `--persistence-backend sqlite` (default, single-node, volume `synthorg-data`) or `postgres` (adds a DHI postgres service on `3002`, volume `synthorg-pgdata`). `SYNTHORG_DATABASE_URL` wins over `SYNTHORG_DB_PATH`; a malformed URL fails loudly. yoyo applies revisions in-process. Ports: 3000 web / 3001 backend / 3002 postgres / 3003 NATS. Detail: [cli-persistence-backends.md](../docs/reference/cli-persistence-backends.md).
