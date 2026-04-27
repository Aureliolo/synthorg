# Configuration Precedence

On-demand reference for how SynthOrg resolves configuration values. The
short rule in `CLAUDE.md` is the contract; this page is the full
exception registry, source matrix, and rationale.

## The rule

For every mutable setting:

```text
1. Settings Database  (SettingsService.get())  -- canonical
2. Environment Variable  (SYNTHORG_<NAMESPACE>_<KEY>)
3. YAML Configuration  (RootConfig)
4. Code Default  (SettingDefinition.default)
```

First match wins. The chain is implemented in
`synthorg.settings.service.SettingsService` and is the **only** sanctioned
way to resolve a runtime-mutable setting.  Direct env-var reads in
application code are forbidden except for the documented exceptions
below.

## Discoverability

Every (namespace, key) emits one INFO `settings.value.resolved` event
on its first cold read per process.  The payload carries `source`
(`db` / `env` / `yaml` / `default`) and `yaml_path` so an operator can
audit at startup which surface supplied each value.  Subsequent
resolutions stay at DEBUG.

## Exception classes

Two sanctioned exceptions to the standard chain.  Both are documented
per-setting in the matrix below.

### Init-time only (no registry entry)

Used for credentials and bootstrap-only paths where a runtime registry
entry would be unsafe or meaningless:

- **Persistence URLs / credentials.** Rotating DB credentials at
  runtime through the settings UI exposes them to every operator with
  `settings:read`, even when `sensitive=True` masks the displayed
  value.  Env-only + secret backend is the safer pattern.
- **Bootstrap secrets.** JWT secret, master key, pagination cursor
  secret -- all read once at process start before the settings service
  exists.

### Read-only post-init (registry entry, mutation rejected)

Used for paths that are baked at process start but should still be
operator-visible.  Registered with `read_only_post_init=True` (which
implies `restart_required=True`); `SettingsService.set()`,
`set_many()`, `delete()`, and `delete_namespace()` raise
`SettingReadOnlyError` so an operator does not believe the override
took effect when the running process keeps the boot-time value.  The
value still resolves through the env -> YAML -> default chain at first
read so the /settings UI shows the truth.

## Source matrix

| Setting | Sources | Init-time? | Notes |
|---|---|---|---|
| `observability.root_log_level` | DB > env > YAML > default | No | Standard mutable. |
| `observability.log_level_console` | DB > env (`SYNTHORG_LOG_LEVEL`) > unset | No | Mutable; overrides only the console sink, not the root logger. |
| `observability.log_directory` | env (`SYNTHORG_LOG_DIR`) > YAML > unset | **Yes** | Read-only-post-init.  Path-traversal still rejected at boot. |
| `communication.nats_url` | env (`SYNTHORG_NATS_URL`) > YAML > default | **Yes** | Read-only-post-init. |
| `workers.count` | env (`SYNTHORG_WORKERS`) > YAML > default | **Yes** | Read-only-post-init. |
| Telemetry opt-in | env (`SYNTHORG_TELEMETRY`) > YAML (`telemetry.enabled`) > default | **Yes** | **No registry entry.**  Read once in `TelemetryCollector.__init__`; runtime mutation has no effect.  Promotion to a registry entry is tracked as follow-up. |
| SQLite path | env (`SYNTHORG_DB_PATH`) > YAML | **Yes** | **No registry entry.**  Init-time exception. |
| Postgres URL | env (`SYNTHORG_DATABASE_URL`) > YAML | **Yes** | **No registry entry.**  Credentials; init-time exception. |
| JWT secret | env (`SYNTHORG_JWT_SECRET`) | **Yes** | **No registry entry.**  Bootstrap secret. |
| Master key | env (`SYNTHORG_MASTER_KEY`) | **Yes** | **No registry entry.**  Bootstrap secret. |
| Pagination cursor secret | env (`SYNTHORG_PAGINATION_CURSOR_SECRET`) | **Yes** | **No registry entry.**  Bootstrap secret. |

For the full inventory of `SYNTHORG_*` env vars, see
[environment-variables.md](environment-variables.md).

## Custom env var names (`env_var_override`)

The default env var name for a registered setting is auto-derived
as ``SYNTHORG_<NAMESPACE>_<KEY>``.  When an established
operator-facing env var name predates this rule (e.g. the
Docker-compose template already sets ``SYNTHORG_LOG_DIR``), the
registry definition can set ``env_var_override="SYNTHORG_LOG_DIR"``
and the resolver will look up that exact name instead.  Settings
currently using overrides:

| Registry key | Override env var |
|---|---|
| `observability/log_directory` | `SYNTHORG_LOG_DIR` |
| `observability/log_level_console` | `SYNTHORG_LOG_LEVEL` |
| `communication/nats_url` | `SYNTHORG_NATS_URL` |
| `workers/count` | `SYNTHORG_WORKERS` |

When `env_var_override` is set, the auto-derived name is **not**
consulted -- only the override.  This keeps the operator surface
clean: there is exactly one env var name per setting.

## Adding a new setting

1. Decide whether the setting is mutable at runtime.
2. **Mutable:** register a normal `SettingDefinition` in the
   appropriate `src/synthorg/settings/definitions/<namespace>.py`
   module.  The env-var override is auto-derived as
   `SYNTHORG_<NAMESPACE>_<KEY>`.
3. **Init-time read-only but operator-visible:** register with
   `restart_required=True` and `read_only_post_init=True`.  The
   `SettingsService` will reject runtime mutation.
4. **Bootstrap secret:** do **not** register.  Read the env var
   directly at the boot site and document the env var on
   [environment-variables.md](environment-variables.md).
5. Consume the value through `ConfigResolver.get_*` (mutable) or read
   the env var via `os.environ` at startup (init-time).  Direct env
   reads in application code outside startup are forbidden.

## Migration path

When a previously-direct env-var read needs to become a registry
entry:

1. Register the setting (mutable or `read_only_post_init` as
   appropriate).
2. Replace the direct `os.environ.get()` call with
   `ConfigResolver.get_*()`.
3. Add a precedence-chain test under
   `tests/unit/settings/test_precedence_chain.py`.
4. Document the new entry in this page's source matrix.
