---
title: CLI Config Subcommands
description: synthorg config get/set/unset/list/path/edit reference, full settable-keys inventory, and tunable value formats.
---

# CLI Config Subcommands

On-demand reference for `synthorg config` operators. The short summary in `cli/CLAUDE.md` is: `synthorg config <subcommand>` exposes get / set / unset / list / path / edit; compose-affecting keys trigger automatic regeneration.

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `show` | Display all current settings (default when no subcommand) |
| `get <key>` | Get a single config value (40 gettable keys) |
| `set <key> <value>` | Set a config value (40 settable keys; compose-affecting keys trigger regeneration) |
| `unset <key>` | Reset a key to its default value |
| `list` | Show all keys with resolved value and source (env / config / default) |
| `path` | Print the config file path |
| `edit` | Open config file in `$VISUAL` / `$EDITOR` |

## Settable keys (full inventory)

`auto_apply_compose`, `auto_cleanup`, `auto_pull`, `auto_restart`, `auto_start_after_wipe`, `auto_update_cli`, `backend_port`, `changelog_view`, `channel`, `color`, `docker_sock`, `fine_tuning`, `fine_tuning_variant`, `hints`, `image_tag`, `log_level`, `output`, `sandbox`, `telemetry_opt_in`, `timestamps`, `web_port`.

Plus the tunables: `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, `nats_image_tag`, `default_nats_stream_prefix`, `backup_create_timeout`, `backup_restore_timeout`, `health_check_timeout`, `self_update_http_timeout`, `self_update_api_timeout`, `tuf_fetch_timeout`, `attestation_http_timeout`, `image_verify_timeout`, `image_pull_attempts`, `image_pull_retry_delay`, `max_api_response_bytes`, `max_binary_bytes`, `max_archive_entry_bytes`.

### Compose-affecting keys (trigger automatic `compose.yml` regeneration)

`backend_port`, `web_port`, `sandbox`, `docker_sock`, `image_tag`, `log_level`, `telemetry_opt_in`, `fine_tuning`, `fine_tuning_variant`, `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, `nats_image_tag`, `default_nats_stream_prefix`.

Toggling `fine_tuning` on requires `sandbox=true` and amd64; validation runs at `config set` time so inconsistent combinations fail before the next `start`.

### Verification-disabling overrides

Overriding any of `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, or `nats_image_tag` transfers trust to the operator: the CLI disables image signature and SLSA provenance verification **for that invocation only** and writes a one-shot warning to stderr on **every** invocation where the override is active.

The warning is **not** suppressed under `--quiet` or `--json`; a safety-critical notice must appear in the audit trail of every scripted run. The pinned SAN regex and DHI digest map are bound to the default values, so verification cannot succeed against a custom deployment target.

## Tunable value formats

- **Durations**: Go `time.ParseDuration` format. Examples: `30s`, `5m`, `1h`, `500ms`. Values must be strictly positive.
- **Byte sizes**: plain integers (`1048576` = 1 MiB) or suffixed values. IEC binary suffixes: `B`, `KiB`, `MiB`, `GiB` (powers of 1024). SI decimal suffixes: `KB`, `MB`, `GB` (powers of 1000). Case-insensitive. Rejected: negative, zero, or values exceeding the 1 GiB runtime ceiling.
- **Integers**: plain decimal integers. Each integer tunable declares its own `[min, max]` range (e.g. `image_pull_attempts` is `[1, 100]`). Rejected: non-numeric values, negatives, or values outside the per-tunable range.
- **Registry hosts**: DNS hostname, optionally with `:port`. Matches `[a-zA-Z0-9][a-zA-Z0-9.-]*(:[0-9]+)?`.
- **Image tags**: Docker tag grammar. Matches `[a-zA-Z0-9][a-zA-Z0-9._-]*`.
- **NATS URLs**: must use `nats://`, `tls://`, or `nats+tls://` scheme and include a host.
- **NATS stream prefix**: uppercase alphanumerics with `_` or `-`. Matches `[A-Z0-9][A-Z0-9_-]*`.

## `changelog_view`

Enum, either `highlights` (default) or `commits`. Sets the default view for the `synthorg update` upgrade walk between installed and target releases. `highlights` shows the AI-generated three-section summary; `commits` shows the Release Please commit-based changelog. Inside the walk, `c` toggles between the two views for the current session without modifying the persisted value.

On the `dev` channel the setting is moot: dev pre-releases have no Highlights block, so the walk always renders a single combined commit list fetched by paginating the GitHub list-commits endpoint (`/repos/.../commits?sha=&per_page=25&page=N`) backwards from the target release until the installed commit SHA is encountered. The compare endpoint is deliberately not used because it inlines a `files[]` patch array per commit and routinely overruns the API response cap on multi-hundred-file release ranges.

When the walk cannot render (network failure, the installed dev pre-release tag was pruned from the remote, or the range is empty) the CLI prints an explicit `Warn` line explaining the cause and falls back to the terse offline notice; it never silently degrades.

## See also

- [cli-env-vars.md](cli-env-vars.md): the env-var equivalents for every settable key plus CLI-only env vars.
- [cli-persistence-backends.md](cli-persistence-backends.md): SQLite / Postgres backend selection and orchestration.
