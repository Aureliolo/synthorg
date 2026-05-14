# Configuration Precedence

On-demand reference for how SynthOrg resolves configuration values. The
short rule in `CLAUDE.md` is the contract; this page is the full
exception registry, source matrix, and rationale.

## The rule

Three sources, in order, first match wins:

```text
1. Settings Database  (per-installation runtime override, set via /settings)
2. Environment Variable  (deployment preset; docker-compose, K8s, .env)
3. Code Default  (SettingDefinition.default)
```

The chain is implemented in `synthorg.settings.service.SettingsService`
and is the **only** sanctioned way to resolve a runtime-mutable
setting. Direct env-var reads in application code are forbidden except
for the documented bootstrap exceptions below.

YAML is **not** a precedence tier. The `company.yaml` file is an
ingestion format for company templates (charter, departments, agents,
workflows). Its contents flow into domain tables on `synthorg init`;
they do not participate in the settings chain.

## The three categories

Every setting belongs to exactly one of three categories. The category
determines which subset of the chain applies.

### Category 1: Standard mutable

Default category. The full chain applies: a `/settings` runtime
override (DB row) wins over an env var, which wins over the registered
code default.

Examples: `observability.root_log_level`, `observability.log_level_console`,
`api.lifecycle_cleanup_enabled`, `engine.timeout_enforcement_enabled`.

### Category 2: Read-only post-init

Registered with `read_only_post_init=True` (which implies
`restart_required=True`). The DB lookup is bypassed on reads, and
`SettingsService.set()`, `set_many()`, `delete()`, and
`delete_namespace()` raise `SettingReadOnlyError` so an operator does
not believe a runtime override took effect when the running process
keeps the boot-time value.

For these entries the precedence chain collapses to **env > default**.
The DB step is bypassed on reads (`get`, `get_namespace`, `get_all`,
`get_page`, `get_versioned`) regardless of whether a stale row exists,
because the running process resolves its bootstrap value once and
holds onto it; a row left over from a pre-rename schema or an ops
mistake on a peer node would otherwise surface a value the runtime no
longer honours. The `/settings` UI therefore reflects the actual
running value, sourced from the env var or registered default at
first read.

The DB-bypass branch lives in `src/synthorg/settings/service.py`
inside `get()` (the `if not definition.read_only_post_init:` guard
around the `_resolve_db` call) and is mirrored in
`_resolve_with_db_lookup` for batch reads (the `if
definition.read_only_post_init: db_hit = None` short-circuit before
the DB row is consulted).

Examples: `api.server_port`, `api.server_host`, `api.api_prefix`,
`communication.nats_url`, `workers.count`, `observability.log_directory`,
`api.cors_allowed_origins`, `api.trusted_proxies`,
`api.rate_limiter_enabled`.

### Category 3: Bootstrap secret (init-time exception)

Read once at process start before `SettingsService` exists. No
registry entry. Pure env. The value is captured into a typed domain
object at the boot site (e.g. `JwtSecret`, `CursorConfig`,
`SettingsEncryptor`, persistence config) and never re-read.

Why not register them as Cat-2 with `sensitive=True`? Two reasons:

- **Persistence URLs and credentials.** Rotating DB credentials at
  runtime through the settings UI exposes them to every operator
  holding `settings:read`, even when `sensitive=True` masks the
  displayed value. Env-only plus a secret backend is the safer
  pattern.
- **Bootstrap secrets.** JWT secret, master key, pagination cursor
  secret, settings encryption key: all read once before the settings
  service exists. A registry entry would be inert for these.

Examples: `SYNTHORG_DATABASE_URL`, `SYNTHORG_DB_PATH`,
`SYNTHORG_POSTGRES_SSL_MODE`, `SYNTHORG_CONFIG_PATH`,
`SYNTHORG_JWT_SECRET`, `SYNTHORG_MASTER_KEY`,
`SYNTHORG_PAGINATION_CURSOR_SECRET`, `SYNTHORG_SETTINGS_KEY`.

## Discoverability

Every (namespace, key) emits one INFO `settings.value.resolved` event
on its first cold read per process. The payload carries `source`
(`db` / `env` / `default`) so an operator can audit at startup which
surface supplied each value. Subsequent resolutions stay at DEBUG.

Category 3 secrets do not emit a `settings.value.resolved` event;
they are read directly at the boot site and logged via the
domain-specific startup event (e.g. `API_APP_STARTUP`,
`SETTINGS_ENCRYPTOR_BOOTSTRAP`).

## Source matrix

### Category 1 examples (DB > env > default)

| Setting | Env override | Notes |
|---|---|---|
| `observability.root_log_level` | `SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL` | Standard mutable. |
| `observability.log_level_console` | `SYNTHORG_LOG_LEVEL` | Mutable; overrides the console sink only. |
| `telemetry.enabled` | `SYNTHORG_TELEMETRY_ENABLED` | Mutable; the collector reads the env var at boot for the fast-path, then honours runtime DB mutations on the next process restart. |
| `engine.timeout_enforcement_enabled` | `SYNTHORG_ENGINE_TIMEOUT_ENFORCEMENT_ENABLED` | Mutable kill-switch. |

### Category 2 examples (env > default; DB bypassed)

| Setting | Env override | Notes |
|---|---|---|
| `api.server_host` | `SYNTHORG_API_SERVER_HOST` | Consumed pre-init via `bootstrap_resolver` at app construction; registry entry for `/settings` discoverability. |
| `api.server_port` | `SYNTHORG_API_SERVER_PORT` | Same as above. |
| `api.api_prefix` | `SYNTHORG_API_API_PREFIX` | Same. |
| `api.cors_allowed_origins` | `SYNTHORG_API_CORS_ALLOWED_ORIGINS` | Same. JSON-encoded list. |
| `api.trusted_proxies` | `SYNTHORG_API_TRUSTED_PROXIES` | Same. JSON-encoded list. |
| `api.rate_limiter_enabled` | `SYNTHORG_API_RATE_LIMITER_ENABLED` | Same. Bool token (`true`/`false`/`1`/`0`/`yes`/`no`). |
| `communication.nats_url` | `SYNTHORG_NATS_URL` | Read once by the bus driver at startup. |
| `workers.count` | `SYNTHORG_WORKERS` | Read at worker-process boot AND by the worker pool builder. |
| `observability.log_directory` | `SYNTHORG_LOG_DIR` | Path-traversal validated at the boot site. |

### Category 3 examples (env only; no registry entry)

| Concern | Env var | Boot site |
|---|---|---|
| SQLite path | `SYNTHORG_DB_PATH` | `api/app.py`, `api/integrations_wiring.py` |
| Postgres URL | `SYNTHORG_DATABASE_URL` | `api/app.py` |
| Postgres SSL mode | `SYNTHORG_POSTGRES_SSL_MODE` | `api/app.py` |
| Config-file path | `SYNTHORG_CONFIG_PATH` | `api/app.py`, `backup/factory.py` |
| JWT secret | `SYNTHORG_JWT_SECRET` | `api/auth/secret.py` |
| Master key (OAuth) | `SYNTHORG_MASTER_KEY` | `integrations/oauth/pkce.py` |
| Pagination cursor secret | `SYNTHORG_PAGINATION_CURSOR_SECRET` | `api/cursor_config.py` |
| Settings encryption key | `SYNTHORG_SETTINGS_KEY` | `settings/encryption.py` |

For the full inventory of `SYNTHORG_*` env vars, see
[environment-variables.md](environment-variables.md).

## Custom env var names (`env_var_override`)

The default env var name for a registered setting is auto-derived as
`SYNTHORG_<NAMESPACE>_<KEY>`. When an established operator-facing env
var name predates this rule (e.g. the Docker-compose template already
sets `SYNTHORG_LOG_DIR`), the registry definition can set
`env_var_override="SYNTHORG_LOG_DIR"` and the resolver will look up
that exact name instead. Settings currently using overrides:

| Registry key | Override env var |
|---|---|
| `observability/log_directory` | `SYNTHORG_LOG_DIR` |
| `observability/log_level_console` | `SYNTHORG_LOG_LEVEL` |
| `communication/nats_url` | `SYNTHORG_NATS_URL` |
| `workers/count` | `SYNTHORG_WORKERS` |
| `tools/sandbox_image` | `SYNTHORG_SANDBOX_IMAGE` |
| `tools/sidecar_image` | `SYNTHORG_SIDECAR_IMAGE` |

When `env_var_override` is set, the auto-derived name is **not**
consulted: only the override. This keeps the operator surface clean:
exactly one env var name per setting.

## Adding a new setting

1. Decide which category fits.
2. **Category 1 (mutable):** register a normal `SettingDefinition` in
   the appropriate `src/synthorg/settings/definitions/<namespace>.py`
   module. The env-var override is auto-derived as
   `SYNTHORG_<NAMESPACE>_<KEY>`; supply `env_var_override=` if an
   operator-facing name predates the rule.
3. **Category 2 (init-time read-only but operator-visible):** register
   with `restart_required=True` and `read_only_post_init=True`. The
   `SettingsService` rejects runtime mutation and bypasses the DB on
   reads.
4. **Category 3 (bootstrap secret):** do **not** register. Read the
   env var directly at the boot site and document the env var on
   [environment-variables.md](environment-variables.md). Capture into
   a typed domain object; never re-read.
5. Consume the value via `ConfigResolver.get_*()` (post-init) or
   `synthorg.settings.bootstrap_resolver.resolve_init_value(...)`
   (pre-init). Direct `os.environ.get` reads in application code
   outside startup are forbidden.

## Bootstrap resolver (pre-`SettingsService` Cat-2 reads)

Some Category-2 settings are consumed at app construction time, before
`SettingsService` has been wired. Examples: rate-limiter middleware
construction, log-sink bootstrap, log-directory selection. Reading
`os.environ` directly at these sites is drift: the registry already
owns the env var name and the default, and the chain (env > default)
should be applied uniformly.

`synthorg.settings.bootstrap_resolver.resolve_init_value(...)` is the
sanctioned pre-init resolver. It reads the `SettingDefinition` from
the registry to obtain the env var name (override or auto-derived)
and the typed default, then returns the env value (if set) or the
registered default. Optional `parse` callback validates and converts
the env string to the consumer's type, returning `None` to fall back
to the default.

```python
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.namespaces import SettingNamespace

resolved = resolve_init_value(
    SettingNamespace.API,
    "rate_limiter_enabled",
    parse=_parse_bool_token,
)
rate_limiter_enabled = resolved.value
```

Used by:

- `synthorg.api.app._build_rate_limiter_enabled` (rate-limiter middleware boot)
- `synthorg.api.app_builders._bootstrap_app_logging` (log directory)
- `synthorg.observability.setup._apply_console_level_override` (console log level)

## Pydantic mirror fields (`apply_settings_mirrors`)

Many Pydantic config classes (`ApiConfig`, `ServerConfig`,
`BudgetConfig`, etc.) carry fields that mirror registered settings.
With YAML eliminated from the precedence chain, the Pydantic-tier
default would otherwise drift from the env-tier override resolved by
`SettingsService`.

### Settings-only registered keys (no Pydantic mirror)

Some registered settings are consumed exclusively through
`SettingsService` (or `ConfigResolver`) and have no corresponding
field on any Pydantic config class. They participate in the standard
precedence chain (DB > env > default) without needing a mirror
declaration. Examples in the `company` namespace:

- `company.name_locales`: consumed in
  `src/synthorg/api/controllers/setup/company_helpers.py` via
  `SettingsService.get_entry`.
- `company.description`: registered for `/settings` UI discoverability;
  no current code consumer.

These keys are NOT fields on `RootConfig`; treating them as
settings-only avoids the dual-surface drift that the mirror pattern
exists to fix.

`synthorg.settings.mirrors.apply_settings_mirrors` is the sanctioned
fix. Each Pydantic class with mirror fields declares them via a
`MirrorField` tuple and attaches a `model_validator(mode="before")`
that populates unset fields from the registry. The Pydantic field
declarations remain (consumer API unchanged) but the value at
construction time IS the precedence-chain result.

```python
from typing import Any, ClassVar
from pydantic import BaseModel, ConfigDict, Field, model_validator
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField, apply_settings_mirrors, parse_bool,
)


class MyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.MYNS,
            key="enabled",
            parse=parse_bool,
        ),
    )

    enabled: bool = Field(default=True)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)
```

### Sentinel-preserving mode: `only_if_env_set=True`

When the Pydantic field's `None` default carries semantic meaning the
registry default would clobber, set `only_if_env_set=True` on the
`MirrorField`. The mirror then fires ONLY when the operator has
explicitly set the env var; if the resolver falls back to the
registered default the Pydantic field keeps its declared default.
Used by:

- `AuthConfig.exclude_paths` (`None` = auto-derive from API prefix)
- `CoordinationSectionConfig.max_concurrency_per_wave` (`None` = unlimited)
- `CeremonyPolicyConfig.{strategy, velocity_calculator, auto_transition, transition_threshold}` (`None` = inherit from level up)

### Selecting between the three resolution helpers

| Use case | Helper |
|---|---|
| Settings consumed at app construction, before `SettingsService` exists | `bootstrap_resolver.resolve_init_value` |
| Settings consumed via a Pydantic `Config` field whose value comes from `RootConfig` | `mirrors.apply_settings_mirrors` |
| Runtime-mutable settings consumed per request | `ConfigResolver.get_*()` (post-init) |
| Hot-reloadable knobs needing one snapshot per process tick | Bridge-config snapshot pattern below |

## Protocol constants are not settings

Wire-protocol numerics such as JSON-RPC error codes
(`JSONRPC_PARSE_ERROR: int = -32700`), framing thresholds, or
specification-mandated limits are NOT operator-tunable policy:
changing the value silently breaks interop with peers that read the
public spec. Express them as typed module-level constants and let
`scripts/check_no_magic_numbers.py` recognise the annotation as the
named-constant signal. Import `Final` directly from `typing`; the
gate matches only the bare names `int`, `float`, `Final`, `Final[int]`,
and `Final[float]`, so qualified forms such as `typing.Final[int]`
still flag. Examples:

```python
from typing import Final

JSONRPC_PARSE_ERROR: int = -32700
A2A_TASK_NOT_FOUND: int = -32001
_MAX_FRAME_SIZE: Final[int] = 16384
```

Do not register these in `settings/definitions/`. The precedence
chain is for values that an operator may legitimately tune; protocol
constants are part of the algorithm.

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
   `synthorg/settings/bridge_configs.py` (e.g. `ApiBridgeConfig`)
   with `model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")`,
   one field per setting it carries, defaults that match the
   registered defaults. The model is the single source of truth for
   the fallback value: no controller carries a duplicate constant.
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
   `model_validate(...)` against the field's `Field(ge=..., le=...)`
   bounds, so an out-of-range value raises `ValidationError` and the
   prior snapshot is retained. Module-load-time guard: every key in
   `_WATCHED` is asserted to exist on the bridge model so a typo or
   rename surfaces at import, not on the next operator hot-reload.

Use this pattern when the setting is hot-reloadable
(`restart_required=False`) but per-request resolver lookup would add
overhead or coupling. For restart-required knobs (e.g.
`ws_auth_timeout_seconds`) the simpler `set_*()` pattern in
`_apply_bridge_config` is sufficient.

## Bootstrap-wiring trace (ghost-wired settings gate)

A registered setting whose consuming machinery exists but is never
instantiated at boot is **ghost-wired**: the value resolves cleanly
through the chain, but no code path that reads it ever runs in
default config. Import-graph traces find the consumer code but miss
that its owning service is never started, so a static "find
references" walk can't distinguish a live consumer from a ghost-wired
one.

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
  is False: in default config the factory returns `None`, the start
  gate short-circuits, and every setting in the factory's gating
  namespace is dead.

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

**Setting -> ghost matchers** (run in order; first hit wins):

1. **Gating-namespace match** (factory ghosts only). Every setting
   whose `namespace` equals the factory's gating namespace is
   ghost-wired when the gating flag's registered default is False.
2. **Class-file containment match** (hardcoded-None ghosts only).
   A setting is ghost-wired iff its `key` appears as a substring in
   the ghost class's source file AND its `namespace` appears in
   that file's path.
3. **Direct ConfigResolver consumer match** (Pattern A; both ghost
   kinds). The lint scans the ghost class's source file for
   `ConfigResolver.get_*("<ns>", "<key>")` calls (resolving both
   string literals AND `SettingNamespace.X.value` references); if
   any (ns, key) matches a registered setting, that setting flags
   as ghost-wired. Catches **cross-namespace consumption**: a ghost
   class in `api/foo.py` that reads `engine.X` would not match
   either gating-namespace or class-file containment, but the
   direct ConfigResolver call surfaces it.

When debugging a Pattern A flag, search the ghost class's source
for `ConfigResolver.get_*("<flagged_ns>", "<flagged_key>")` calls and
verify whether the consumer should migrate to a real
unconditionally-started service or whether the gating service
should be wired at boot.

`read_only_post_init=True` settings are skipped by design (registry
entry exists for `/settings` UI introspection; mutation is rejected
at runtime, no live consumer required).

### Suppression marker

Per-setting opt-out: append a trailing comment on the
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
`<key>:<kind>:<owning_class>`, sorted lexicographically.

Lint behaviour:

- Pass when current violations are a subset of baseline.
- Fail (exit 1) listing only the new violations when current is not a subset of baseline.
- Warn (stderr) but pass when baseline contains stale entries (a
  fix landed and the violation no longer exists). Regenerate the
  baseline via `--update-baseline` once the wiring is fixed.

```bash
uv run python scripts/check_setting_to_startup_trace.py
uv run python scripts/check_setting_to_startup_trace.py --update-baseline
```

`--update-baseline` requires explicit user approval to commit the
diff. Don't run it casually: the baseline is the lint's frozen
authority.

## Kill-Switch Idiom (MANDATORY)

Every long-running async loop in `src/synthorg/` MUST be pause-able
at runtime via an `<namespace>.<service>_enabled` boolean setting,
without restarting the process. The canonical shape:

1. Register the flag in `src/synthorg/settings/definitions/<ns>.py`
   with `SettingType.BOOLEAN`, `default="true"`, and a `description`
   that names the gated service. The setting participates in the
   full DB > env > default precedence chain.
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
       except (MemoryError, RecursionError):
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
covered by project convention and reviewed by CodeRabbit / human
review, but they sit outside the AST gate's loop-shaped detection.

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
