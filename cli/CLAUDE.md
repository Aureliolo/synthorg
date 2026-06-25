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
| `--skip-verify` | | `SYNTHORG_NO_VERIFY` / `SYNTHORG_SKIP_VERIFY` | Skip image signature verification |
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

- **Teardown contract**: `wipe`/`uninstall` load via `config.LoadForTeardown` (never strict `Validate`), tolerate a missing compose.yml/Docker, never refuse on invalid config; the binary installs to a sibling tree, never inside the data dir, so teardown uses a plain `os.RemoveAll`. Add new teardown code to this best-effort pattern (`teardown_shared.go`).
- **Env vars + config keys**: 41 settable config keys via `synthorg config <show|get|set|import|unset|list|path|edit>` (set applies atomically); `SYNTHORG_*` env vars cover backend/channel, image/registry (overriding registry/image keys disables verification for that invocation + warns), timeout/retry tuning, and byte caps. `Default{Postgres,NATS}Image{Tag,Digest}` in `cli/internal/config/state.go` is the single source of truth (Renovate-managed; hand-mirror `docker/compose.yml`, enforced by `compose_sync_test.go`). Full tables: [cli-env-vars.md](../docs/reference/cli-env-vars.md), [cli-config-subcommands.md](../docs/reference/cli-config-subcommands.md).
- **Doctor**: `synthorg doctor [--checks name,...] [--fix]` collects diagnostics, saves a timestamped `0600` report under the data dir. Categories: environment, health, containers, images, compose, config, disk, errors.
- **Persistence backends**: `--persistence-backend sqlite` (default, single-node, volume `synthorg-data`) or `postgres` (adds a DHI postgres service on `3002`, volume `synthorg-pgdata`). `SYNTHORG_DATABASE_URL` wins over `SYNTHORG_DB_PATH`; a malformed URL fails loudly. yoyo applies revisions in-process. Ports: 3000 web / 3001 backend / 3002 postgres / 3003 NATS. Detail: [cli-persistence-backends.md](../docs/reference/cli-persistence-backends.md).
