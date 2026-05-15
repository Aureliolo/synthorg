---
title: CLI Per-Command Flags
description: Operator reference for synthorg subcommands and their flags, with defaults and tunable env-var overrides.
---

# CLI Per-Command Flags

Operator reference for the `synthorg` CLI binary. The CLI is Docker-only: `init` provisions the deployment, `start` / `stop` / `update` manage the container lifecycle, `status` / `logs` / `doctor` introspect, `backup` / `wipe` / `cleanup` cover data and image hygiene. Feature work runs in the web dashboard against the REST API.

Defaults shown below are compiled into the binary; tunable defaults are also reachable via `synthorg config set <key> <value>` and the matching `SYNTHORG_*` env var.

## Lifecycle

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `init` | `--backend-port` | (prompted) | Host port for the REST / WebSocket API |
| `init` | `--web-port` | (prompted) | Host port for the web dashboard |
| `init` | `--sandbox` | (prompted) | Mount the Docker socket for the agent code-execution sandbox |
| `init` | `--log-level` | (prompted) | Backend log level (`debug` / `info` / `warn` / `error`) |
| `init` | `--image-tag` | (pinned) | Override container image tag |
| `init` | `--channel` | `stable` | Release channel (`stable` / `dev`) |
| `init` | `--bus-backend` | `inproc` | Communication bus backend (`inproc` / `nats`) |
| `init` | `--persistence-backend` | `sqlite` | Persistence backend (`sqlite` / `postgres`) |
| `init` | `--postgres-port` | `5432` | Host port when `--persistence-backend=postgres` |
| `init` | `--encrypt-secrets` | `true` | Encrypt connection secrets at rest via Fernet (`true` / `false`) |
| `start` | `--no-wait` | `false` | Skip the health check after start |
| `start` | `--timeout` | `90s` | Health-check timeout (Go duration, e.g. `90s`, `2m`) |
| `start` | `--no-pull` | `false` | Skip image verification and pull |
| `start` | `--dry-run` | `false` | Show what would happen without executing |
| `start` | `--no-detach` | `false` | Run in foreground (stream logs; Ctrl+C to stop) |
| `start` | `--no-verify` | `false` | Skip image signature verification (also `--skip-verify`) |
| `stop` | `--timeout` / `-t` | `10s` | Graceful shutdown timeout |
| `stop` | `--volumes` | `false` | Remove named volumes (destructive) |
| `update` | `--dry-run` | `false` | Show what would happen without executing |
| `update` | `--no-restart` | `false` | Pull images but do not restart containers |
| `update` | `--timeout` | `90s` | Health-check and verification timeout |
| `update` | `--cli-only` | `false` | Only update the CLI binary |
| `update` | `--images-only` | `false` | Only update container images (skip CLI) |
| `update` | `--check` | `false` | Check for updates and exit (`0`=current, `10`=available) |

## Introspection

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `status` | `--watch` / `-w` | `false` | Continuously poll status |
| `status` | `--interval` | `2s` | Watch polling interval (Go duration) |
| `status` | `--wide` | `false` | Show extra columns (ports) |
| `status` | `--no-trunc` | `false` | Show full image names |
| `status` | `--services` | (all) | Filter by service names (comma-separated) |
| `status` | `--check` | `false` | Exit code only (`0`=healthy, `3`=unhealthy, `4`=unreachable) |
| `logs` | `--follow` / `-f` | `false` | Follow log output |
| `logs` | `--tail` | `100` | Number of lines to show from the tail |
| `logs` | `--since` | (none) | Show logs since timestamp or relative (e.g. `1h`, `2024-01-01`) |
| `logs` | `--until` | (none) | Show logs until timestamp or relative |
| `logs` | `--timestamps` / `-t` | `false` | Show timestamps |
| `logs` | `--no-log-prefix` | `false` | Suppress the service prefix |
| `doctor` | `--checks` | `all` | Comma-separated check categories (`environment`, `health`, ...) |
| `doctor` | `--fix` | `false` | Auto-fix detected issues where supported |
| `version` | `--short` | `false` | Print only the version string |

## Hygiene

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `cleanup` | `--dry-run` | `false` | List images without removing |
| `cleanup` | `--all` | `false` | Include ALL SynthOrg images, not just old ones |
| `cleanup` | `--keep` | `0` | Keep N most recent previous versions (`0`=remove all old) |
| `wipe` | `--dry-run` | `false` | Show what would happen without executing |
| `wipe` | `--no-backup` | `false` | Skip the interactive backup prompt |
| `wipe` | `--keep-images` | `false` | Leave container images on disk |
| `uninstall` | `--keep-data` | `false` | Leave the data directory in place |
| `uninstall` | `--keep-images` | `false` | Leave container images on disk |

## Backup

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `backup create` | `--output` / `-o` | (data dir) | Output path for the backup archive |
| `backup create` | `--timeout` | `60s` | API request timeout (Go duration). Default is the `backup_create_timeout` tunable. |
| `backup list` | `--limit` / `-n` | `0` | Show N most recent (`0`=all) |
| `backup list` | `--sort` | `newest` | Sort order (`newest` / `oldest` / `size`) |
| `backup restore` | `--confirm` | (required) | Required acknowledgement that restore overwrites current data |
| `backup restore` | `--dry-run` | `false` | Validate the archive without overwriting |
| `backup restore` | `--no-restart` | `false` | Restore without restarting containers afterwards |
| `backup restore` | `--timeout` | `30s` | API request timeout (Go duration). Default is the `backup_restore_timeout` tunable. |

## Workers

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `worker start` | `--workers` | `4` | Number of worker processes |
| `worker start` | `--nats-url` | `nats://nats:4222` | NATS URL (also `SYNTHORG_NATS_URL`) |
| `worker start` | `--stream-prefix` | `SYNTHORG` | NATS stream prefix (also `SYNTHORG_DEFAULT_NATS_STREAM_PREFIX`) |
| `worker start` | `--container` | (empty) | Container name; falls back to `synthorg-backend` when unset |

## Completion

| Command | Description |
|---------|-------------|
| `completion <shell>` | Emit autocompletion script (`bash` / `zsh` / `fish` / `powershell`) |
| `completion-install <shell>` | Write the autocompletion script into your shell startup |

## Scaffolding

| Command | Flag | Default | Description |
|---------|------|---------|-------------|
| `new <kind> <domain>` | `--dry-run` | `false` | Print the file plan without writing |
| `new <kind> <domain>` | `--overwrite` | `false` | Overwrite existing files in the target |

`<kind>` is one of `service` / `persistence` / `tool` / `controller`. See [scaffolding.md](scaffolding.md) for the per-kind file set and the convention enforcement coverage.

## See also

- [cli-config-subcommands.md](cli-config-subcommands.md): `synthorg config` get / set / unset and the full settable-key inventory.
- [cli-env-vars.md](cli-env-vars.md): the `SYNTHORG_*` env var inventory consumed by the CLI binary.
- [cli-persistence-backends.md](cli-persistence-backends.md): SQLite vs Postgres orchestration.
