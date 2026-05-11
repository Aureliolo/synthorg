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
took effect when the running process keeps the boot-time value.

For these entries, the precedence chain collapses to **env > YAML >
default** -- the DB row is never consulted on reads (`get`,
`get_namespace`, `get_all`, `get_page`, `get_versioned`).  The DB step
is bypassed regardless of whether a stale row exists, because the
running process resolves its bootstrap value once and holds onto it;
a row left over from a pre-rename schema or an ops mistake on a peer
node would otherwise surface a value the runtime no longer honours.
The /settings UI therefore reflects the actual running value, sourced
from the env var or YAML at first read.

## Source matrix

| Setting | Sources | Init-time? | Notes |
|---|---|---|---|
| `observability.root_log_level` | DB > env > YAML > default | No | Standard mutable. |
| `observability.log_level_console` | DB > env (`SYNTHORG_LOG_LEVEL`) > unset | No | Mutable; overrides only the console sink, not the root logger. |
| `observability.log_directory` | env (`SYNTHORG_LOG_DIR`) > YAML > unset | **Yes** | Read-only-post-init (DB bypassed on reads).  Path-traversal still rejected at boot. |
| `communication.nats_url` | env (`SYNTHORG_NATS_URL`) > YAML > default | **Yes** | Read-only-post-init (DB bypassed on reads). |
| `workers.count` | env (`SYNTHORG_WORKERS`) > YAML > default | **Yes** | Read-only-post-init (DB bypassed on reads). |
| `api.api_prefix` | env (`SYNTHORG_API_API_PREFIX`) > YAML (`api.api_prefix`) > default (`/api/v1`) | **Yes** | Read-only-post-init.  Consumed via `RootConfig.api.api_prefix` at app construction; registry entry exists for /settings discoverability only. |
| `api.server_host` | env (`SYNTHORG_API_SERVER_HOST`) > YAML (`api.server.host`) > default (`127.0.0.1`) | **Yes** | Read-only-post-init.  Consumed via `RootConfig.api.server.host` at boot; registry entry for discoverability only. |
| `api.server_port` | env (`SYNTHORG_API_SERVER_PORT`) > YAML (`api.server.port`) > default (`3001`) | **Yes** | Read-only-post-init.  Consumed via `RootConfig.api.server.port` at boot; registry entry for discoverability only. |
| `api.cors_allowed_origins` | env (`SYNTHORG_API_CORS_ALLOWED_ORIGINS`) > YAML (`api.cors.allowed_origins`) > default (`[]`) | **Yes** | Read-only-post-init.  Consumed via `RootConfig.api.cors` at app construction; registry entry for discoverability only. |
| `api.trusted_proxies` | env (`SYNTHORG_API_TRUSTED_PROXIES`) > YAML (`api.server.trusted_proxies`) > default (`[]`) | **Yes** | Read-only-post-init.  Consumed via `RootConfig.api.server.trusted_proxies` at boot; registry entry for discoverability only. |
| `telemetry.enabled` | env (`SYNTHORG_TELEMETRY_ENABLED`) > YAML (`telemetry.enabled`) > default (`false`) | No | Standard mutable.  Registered in `synthorg.settings.definitions.telemetry`; the `/settings` API can introspect and edit it.  The collector reads the env var at boot for the fast-path; runtime DB mutations take effect on the next process restart. |
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

## Protocol constants are not settings

Wire-protocol numerics such as JSON-RPC error codes
(`JSONRPC_PARSE_ERROR: int = -32700`), framing thresholds, or
specification-mandated limits are NOT operator-tunable policy: changing
the value silently breaks interop with peers that read the public
spec. Express them as typed module-level constants and let
`scripts/check_no_magic_numbers.py` recognise the annotation as the
named-constant signal. Examples:

```python
JSONRPC_PARSE_ERROR: int = -32700
A2A_TASK_NOT_FOUND: int = -32001
_MAX_FRAME_SIZE: Final[int] = 16384
```

Do not register these in `settings/definitions/`. The precedence
chain is for values that an operator may legitimately tune; protocol
constants are part of the algorithm.

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

## Bridge-config snapshot pattern (hot-reloadable AppState fields)

For controller / service knobs that should be hot-reloadable but cost
too much to resolve through `ConfigResolver.get_*()` on every request,
the canonical pattern is a frozen Pydantic snapshot on `AppState`
populated at startup and hot-swapped by a settings subscriber on
operator-driven changes. Reference implementation:
`api.max_lifecycle_events_per_query` consumed by
`ActivityController.list_activities`.

The pattern has four pieces:

1. **Frozen bridge model.** A class in
   `synthorg/settings/bridge_configs.py` (e.g. `ApiBridgeConfig`) with
   `model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")`,
   one field per setting it carries, defaults that match the
   registered defaults. The model is the single source of truth for
   the fallback value -- no controller carries a duplicate constant.
2. **Resolver builder.** `ConfigResolver.get_<ns>_bridge_config()`
   resolves every field at once via `_resolve_bridge_fields()`.
3. **AppState slot + accessors.** `AppState.__init__` default-
   constructs the bridge model so consumers always see a valid
   snapshot, even before `_apply_bridge_config` has run.
   `AppState.<name>_bridge_config` returns the current snapshot;
   `AppState.swap_<name>_bridge_config(config)` does a wholesale
   replace under a per-bridge `threading.Lock`;
   `AppState.mutate_<name>_bridge_config({field: value, ...})`
   applies a partial update under the same lock so two concurrent
   subscribers cannot lose each other's writes.
4. **Settings subscriber.** A `SettingsSubscriber` implementation in
   `synthorg/settings/subscribers/<name>_bridge_subscriber.py` whose
   `_WATCHED` set lists every hot-reloadable field. On change, the
   subscriber resolves the new value and calls `mutate_*` with the
   single-field update; `mutate_*` re-validates the merged dict via
   `model_validate(...)` (Pydantic v2 skips validators on the bare
   `model_copy(update=...)` path) against the field's
   `Field(ge=..., le=...)` bounds, so an out-of-range value raises
   `ValidationError` and the prior snapshot is retained.
   Module-load-time guard: every key in `_WATCHED` is asserted to
   exist on the bridge model so a typo or rename surfaces at import,
   not on the next operator hot-reload.

Use this pattern when the setting is hot-reloadable
(`restart_required=False`) but per-request resolver lookup would
add overhead or coupling. For restart-required knobs (e.g.
`ws_auth_timeout_seconds`) the simpler `set_*()` pattern in
`_apply_bridge_config` is sufficient.

## Bootstrap-wiring trace (ghost-wired settings gate)

A registered setting whose consuming machinery exists but is never
instantiated at boot is **ghost-wired** -- the value resolves cleanly
through the chain, but no code path that reads it ever runs in default
config. Import-graph traces find the consumer code but miss that its
owning service is never started, so a static "find references" walk
can't distinguish a live consumer from a ghost-wired one.

`scripts/check_setting_to_startup_trace.py` is the standing gate.
Pre-push + CI; mirrors `check_persistence_boundary.py` shape.

### What it catches

The lint detects two ghost-service patterns in lifecycle/app wiring,
then matches settings to those ghosts via three matchers (first hit
wins). Settings unrelated to a known ghost service pass silently;
the lint never flags a setting in isolation.

**Ghost-service patterns:**

- **Hardcoded-None ghost.** A service variable
  `x: T | None = None` paired with a conditional
  `if x is not None: x.start()`. The guard always evaluates False,
  so any setting consumed inside the would-be service is dead at
  runtime even though the consumer code exists.
- **Factory-gated ghost.** A factory `build_x(config) -> T | None`
  whose `None` branch fires when a registered default-disabled flag
  is False -- in default config the factory returns `None`, the
  start gate short-circuits, and every setting in the factory's
  gating namespace is dead.

**Fixing a ghost-wired service** means: drop the factory's early
return (or the hardcoded `None`), construct the service
unconditionally, gate the *behaviour* internally on the runtime
flag, and wire a live `SettingsSubscriber` so operator changes take
effect without restart. See `BackupService` (`backup/factory.py` +
`backup/service.py` + `BackupSettingsSubscriber`) and
`ApprovalTimeoutScheduler` (constructed in `api/app.py`, interval
applied at boot via `_apply_security_timeout_interval` in
`lifecycle_helpers.py`, live-tuned via
`SecurityTimeoutSettingsSubscriber`) for end-to-end references.

**Setting → ghost matchers** (run in order; first hit wins):

1. **Gating-namespace match** (factory ghosts only). Every setting
   whose `namespace` equals the factory's gating namespace is
   ghost-wired when the gating flag's registered default is False.
2. **Class-file containment match** (hardcoded-None ghosts only).
   A setting is ghost-wired iff its `key` appears as a substring
   in the ghost class's source file AND its `namespace` appears
   in that file's path.
3. **Direct ConfigResolver consumer match** (Pattern A; both ghost
   kinds). The lint scans the ghost class's source file for
   `ConfigResolver.get_*("<ns>", "<key>")` calls (resolving both
   string literals AND `SettingNamespace.X.value` references); if
   any (ns, key) matches a registered setting, that setting flags
   as ghost-wired. Catches **cross-namespace consumption** -- a
   ghost class in `api/foo.py` that reads `engine.X` would not
   match either gating-namespace or class-file containment, but
   the direct ConfigResolver call surfaces it.

When debugging a Pattern A flag, search the ghost class's source
for `ConfigResolver.get_*("<flagged_ns>", "<flagged_key>")` calls
and verify whether the consumer should migrate to a real
unconditionally-started service or whether the gating service
should be wired at boot.

`read_only_post_init=True` settings are skipped by design (registry
entry exists for `/settings` UI introspection; mutation is rejected
at runtime, no live consumer required).

### Suppression marker

Per-setting opt-out -- append a trailing comment on the
`_r.register(...)` closing line:

```python
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.X,
        key="discoverability_only_setting",
        ...,
    )
)  # lint-allow: bootstrap-wiring -- explanation here
```

The justification after `--` is required and must be non-empty.
Mirrors the `# lint-allow: persistence-boundary` contract.

### Baseline file

`scripts/setting_to_startup_trace_baseline.txt` freezes the
pre-existing violations so the lint can ship without forcing the
wiring fix in the same PR. Format: one entry per line,
`<yaml_path>:<kind>:<owning_class>`, sorted lexicographically.

Lint behaviour:

- Pass when current violations are a subset of baseline.
- Fail (exit 1) listing only the new violations when current ⊄ baseline.
- Warn (stderr) but pass when baseline contains stale entries (a
  fix landed and the violation no longer exists). Regenerate the
  baseline via `--update-baseline` once the wiring is fixed.

```bash
uv run python scripts/check_setting_to_startup_trace.py
uv run python scripts/check_setting_to_startup_trace.py --update-baseline
```

## Kill-Switch Idiom (MANDATORY)

Every long-running async loop in `src/synthorg/` MUST be pause-able
at runtime via an `<namespace>.<service>_enabled` boolean setting,
without restarting the process. The canonical shape:

1. Register the flag in `src/synthorg/settings/definitions/<ns>.py`
   with `SettingType.BOOLEAN`, `default="true"`, a `description`
   that names the gated service, and a `yaml_path="<ns>.<x>_enabled"`
   so the setting participates in the full DB > env > YAML > default
   precedence chain. Without `yaml_path` the YAML leg is silently
   skipped and operators get the code default at startup.
2. Add a fail-safe-to-enabled resolver helper next to the loop. The
   "no resolver wired" fast-path returns `True` directly so a service
   constructed in a test or pre-startup context (where
   `app_state.has_config_resolver` is `False` / `config_resolver is None`)
   does not crash on a `None.get_bool` access:

   ```python
   async def _resolve_<x>_enabled(...) -> bool:
       if not app_state.has_config_resolver:
           return True
       try:
           return await app_state.config_resolver.get_bool(<ns>, "<x>_enabled")
       except asyncio.CancelledError:
           raise
       except MemoryError, RecursionError:
           raise
       except Exception as exc:
           logger.warning(<event>, error_type=type(exc).__name__,
                          error=safe_error_description(exc))
           return True
   ```

3. Gate the loop body per iteration (or per call for non-loop
   surfaces like `NotificationDispatcher.dispatch`):

   ```python
   while not self._stop_event.is_set():
       if await self._resolve_enabled():
           await self._do_work()
       else:
           logger.debug(<paused_event>, reason="paused_by_setting")
       await asyncio.sleep(self._interval)
   ```

The fail-safe-to-enabled rule is non-negotiable: a settings-backend
outage must not silently silence the surface. Operators silence by
setting the value explicitly.

Reference implementations (symbol-only references; line numbers churn):
`api.lifecycle_helpers._ticket_cleanup_loop`,
`api.lifecycle_helpers._audit_retention_loop`,
`api.webhook_cleanup._webhook_receipt_cleanup_loop`,
`providers.health_prober.ProviderHealthProber._run_loop`,
`notifications.dispatcher.NotificationDispatcher.dispatch`,
`communication.conflict_resolution.escalation.sweeper.EscalationExpirationSweeper._run`.

Per-line opt-out:
`# lint-allow: long-running-loop-kill-switch -- <reason>` on the
`while` line itself, or on one of the two preceding source lines
(leading comment block / decorator). The justification is mandatory
and must be non-empty (mirrors the existing `# lint-allow:`
markers). Suppression is per-loop: a function with two unguarded
long-running loops needs two markers, otherwise a function-wide
opt-out could silently mask a new sibling loop added later.
Pre-existing not-yet-pause-able loops live in
`scripts/long_running_loops_kill_switch_baseline.txt`; the gate
fails when a NEW loop missing the kill-switch lands.

Enforced by `scripts/check_long_running_loops_have_kill_switch.py`
(pre-push + CI). Scope: the gate scans every long-running
`while True:` / `while not <stop_event>.is_set():` inside an
`async def` under `src/synthorg/`, so the loop-bodied surfaces above
(`_ticket_cleanup_loop`, `ProviderHealthProber._run_loop`,
`_webhook_receipt_cleanup_loop`) are lint-enforced. Per-call
non-loop surfaces such as `NotificationDispatcher.dispatch` are
covered by project convention and reviewed by CodeRabbit /
human review, but they sit outside the AST gate's
loop-shaped detection.

## Sandbox image cache

The Pydantic field defaults in
`src/synthorg/tools/sandbox/docker_config.py` no longer read
`SYNTHORG_SANDBOX_IMAGE` / `SYNTHORG_SIDECAR_IMAGE` directly from
`os.environ`; the canonical resolution path is
`tools.sandbox_image` / `tools.sidecar_image` registered in
`definitions/tools.py` with `env_var_override=` matching the
historical env var names. `_apply_bridge_config` resolves both
once at startup and writes them into the process-singleton cache
in `tools/sandbox/_image_resolution.py`. Tests override the cache
via `set_resolved_*_image(...)`; the autouse fixture
`_isolate_sandbox_image_resolution` in
`tests/unit/tools/sandbox/conftest.py` clears the cache around
every sandbox test.

`--update-baseline` requires explicit user approval to commit the
diff. Don't run it casually -- the baseline is the lint's frozen
authority.
