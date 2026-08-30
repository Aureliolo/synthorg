---
title: CLI Config Subcommands
description: synthorg config show/get/set/import/unset/list/path/edit reference, full settable-keys inventory, and tunable value formats.
---

# CLI Config Subcommands

On-demand reference for `synthorg config` operators. The short summary in `cli/CLAUDE.md` is: `synthorg config <subcommand>` exposes show / get / set / import / unset / list / path / edit; compose-affecting keys trigger automatic regeneration.

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `show` | Display all current settings (default when no subcommand) |
| `get <key>` | Get a single config value (includes the read-only `memory_backend` and `persistence_backend`) |
| `set <key> <value> [<key> <value> ...]` | Set one or more config values atomically (compose-affecting keys trigger regeneration) |
| `import <file>` | Apply many values from a `key=value` file atomically (file-driven batch `set`) |
| `unset <key>` | Reset a key to its default value |
| `list` | Show all keys with resolved value and source (env / config / default) |
| `path` | Print the config file path |
| `edit` | Open config file in `$VISUAL` / `$EDITOR` |

`set` and `import` apply all pairs atomically: if any key or value is invalid, nothing is written. Both also auto-generate the Fernet `master_key` when `encrypt_secrets` is true and none exists (exactly as `init` does on save), so config can be pre-seeded before `synthorg init`. The `import` file format is one `key=value` per line; blank lines and `#` comments are ignored and surrounding whitespace is trimmed.

### Reading a setting this binary does not recognise

`show`, `get` and `list` report the value **in effect**, which is not always the value on disk. A closed-set field holding something this release no longer accepts (typically after an upgrade dropped an option) is replaced with the default at load time so that a stale value cannot lock you out of the commands that repair it. Every substitution is announced on stderr on each invocation, naming the field, the rejected value and what is now in effect, and `synthorg doctor` lists them under its config section.

The file itself is left untouched, so the original value is still there to read: a substitution is a fact about one load, not a rewrite of your configuration. Any command that persists state writes the substituted value through, at which point the warning stops on its own. `set` is unaffected and stays strict, so a typo is still refused at the point of entry.

The exceptions are `persistence_backend` and `memory_backend`. Those select where your data lives, and defaulting them would silently point the stack at a different (empty) database, so an unrecognised value there fails the load instead. `doctor` and `config show` still read the file and report it; fix the value with `synthorg config set` or re-run `synthorg init`, which preserves every secret.

## Settable keys (full inventory)

`auto_apply_compose`, `auto_cleanup`, `auto_pull`, `auto_restart`, `auto_start_after_wipe`, `auto_update_cli`, `backend_port`, `changelog_view`, `channel`, `color`, `docker_sock`, `fine_tuning`, `fine_tuning_variant`, `hints`, `image_tag`, `log_level`, `output`, `sandbox`, `telemetry_opt_in`, `timestamps`, `web_port`.

Plus the tunables: `registry_host`, `image_repo_prefix`, `dhi_registry`, `postgres_image_tag`, `nats_image_tag`, `default_nats_stream_prefix`, `backup_create_timeout`, `backup_restore_timeout`, `health_check_timeout`, `health_wait_timeout`, `self_update_http_timeout`, `self_update_api_timeout`, `tuf_fetch_timeout`, `attestation_http_timeout`, `image_verify_timeout`, `image_pull_attempts`, `image_pull_retry_delay`, `health_poll_interval`, `health_initial_delay`, `dhi_verify_timeout`, `update_health_timeout`, `completion_probe_timeout`, `diagnostics_dial_timeout`, `status_docker_timeout`, `max_api_response_bytes`, `max_binary_bytes`, `max_archive_entry_bytes`.

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
- **Image repository prefixes**: lowercase Docker repository path grammar. Matches `[a-z0-9][a-z0-9._/-]*`, up to 255 characters.
- **Image tags**: Docker tag grammar. Matches `[a-zA-Z0-9][a-zA-Z0-9._-]*`.
- **NATS URLs**: must use `nats://`, `tls://`, or `nats+tls://` scheme and include a host.
- **NATS stream prefix**: uppercase alphanumerics with `_` or `-`. Matches `[A-Z0-9][A-Z0-9_-]*`.

## `auto_update_cli`

Boolean, default `false`. Answers the `synthorg update` install confirm (`Update CLI from vX to vY?`) with yes, and prints the changelog instead of paging it, so an update runs start to finish without a key press while still showing what it installs. The install announces that the setting answered for you, and that line survives `--quiet` (the mode an unattended run is invoked in), so an install nobody confirmed always leaves a record saying so.

It covers the CLI binary alone. The compose apply, image pull and container restart that follow keep their own keys (`auto_apply_compose`, `auto_pull`, `auto_restart`), so a fully unattended `synthorg update` sets all four.

## `changelog_view`

Enum, either `highlights` (default) or `commits`. Sets the default view for the `synthorg update` changelog between installed and target releases. `highlights` shows the AI-generated tagline plus two-section summary; `commits` shows the Release Please commit-based changelog. In the interactive walk, `c` toggles between the two views for the current session without modifying the persisted value.

The changelog is presented one of three ways, decided per invocation:

| Presentation | When | Behaviour |
|---|---|---|
| Interactive walk | a terminal on **both** stdin and stdout, no `--yes` or `--quiet`, `auto_update_cli` off | scrollable pager, advanced with `enter` |
| Static | anything else: `auto_update_cli` on, `--yes`, `--quiet`, stdin not a terminal (piped or redirected), or stdout redirected to a file or pipe | everything printed in full, no keys, no height cap |
| Suppressed | `--json` | no human-readable output at all |

Both triggers on the input side count independently: `synthorg update < /dev/null` renders static even on a terminal with no flags set, because there is no one there to answer a pager.

On the stable channel the interactive walk moves three releases at a time; the dev channel has no batches, being one continuous commit list either way (see below).

Only `--json` loses the content, and `--quiet` is deliberately not an exception: what an update is about to install is worth showing even when nothing is going to ask about it, so every other non-interactive context downgrades to the static print rather than dropping to a one-line notice. That covers the whole presentation, not just the release bodies: the summary index, the per-release publish dates and markers, and the fallback notice on a fetch failure all survive `--quiet` too, because half a record is worse than none.

On the `dev` channel `changelog_view` is moot: dev pre-releases have no Highlights block, so the changelog is always a single combined commit list fetched by paginating the GitHub list-commits endpoint (`/repos/.../commits?sha=&per_page=25&page=N`) backwards from the target release until the installed commit SHA is encountered. The compare endpoint is deliberately not used because it inlines a `files[]` patch array per commit and routinely overruns the API response cap on multi-hundred-file release ranges.

When the changelog cannot be fetched (network failure, the installed dev pre-release tag was pruned from the remote, or the range is empty) the CLI prints an explicit `Warn` line explaining the cause and falls back to the terse offline notice; it never silently degrades.

## See also

- [cli-env-vars.md](cli-env-vars.md): the env-var equivalents for every settable key plus CLI-only env vars.
- [cli-persistence-backends.md](cli-persistence-backends.md): SQLite / Postgres backend selection and orchestration.
