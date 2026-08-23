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

### Category 2: Compose-set

Registered with `compose_set=True`. The DB lookup is bypassed on reads, and
`SettingsService.set()`, `set_many()`, and `delete()` raise
`SettingReadOnlyError` rather than store a value the running process will
never read. `delete_namespace()` does not raise: a compose-set key in the
target namespace is logged as a WARNING (`reason="compose_set_swept"`) and
skipped, so it cannot hold the writable overrides the operator wants to clear
hostage.

For these entries the precedence chain collapses to **env > default**.
The DB step is bypassed on reads (`get`, `get_namespace`, `get_all`,
`get_page`, `get_versioned`) regardless of whether a stale row exists,
because the running process resolves its bootstrap value once and
holds onto it; a row left over from a pre-rename schema or an ops
mistake on a peer node would otherwise surface a value the runtime no
longer honours. The `/settings` UI therefore reflects the actual
running value sourced from the env var or registered default at
first read.

The DB-bypass branch lives in `src/synthorg/settings/service.py`
inside `get()` (the `if not definition.compose_set:` guard around the
`_resolve_db` call) and is mirrored in `_resolve_with_db_lookup` for
batch reads (the `if definition.compose_set: db_hit = None`
short-circuit before the DB row is consulted).

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
| `telemetry.enabled` | `SYNTHORG_TELEMETRY_ENABLED` | Mutable; the collector reads the env var at boot for the fast-path, and a DB write applies at once through `TelemetrySettingsSubscriber`. |
| `engine.timeout_enforcement_enabled` | `SYNTHORG_ENGINE_TIMEOUT_ENFORCEMENT_ENABLED` | Mutable kill-switch. |
| `providers.model_refresh_mode` | `SYNTHORG_PROVIDERS_MODEL_REFRESH_MODE` | Config discriminator for the periodic model-refresh subsystem (`off` / `manual_only` / `detect_only` / `reconcile_recommend`); `off` is the safe default. The scheduler re-reads it every tick (fail-safe to `off`), so mode changes apply without a restart. |
| `providers.model_refresh_interval_seconds` | `SYNTHORG_PROVIDERS_MODEL_REFRESH_INTERVAL_SECONDS` | Cadence between automatic reconcile cycles (60s..604800s). Re-read by the scheduler each tick (like the mode), so a change applies on the next cycle without a restart. |
| `providers.model_refresh_auto_apply_within_family` | `SYNTHORG_PROVIDERS_MODEL_REFRESH_AUTO_APPLY_WITHIN_FAMILY` | Opt-in (default off) auto-apply of strictly in-family upgrades; re-read every cycle. |
| `chief_of_staff.propose_enabled` | `SYNTHORG_CHIEF_OF_STAFF_PROPOSE_ENABLED` | On-by-default conversational capability; live-gated per request via `ensure_feature_enabled` (no restart). The siblings `explain_chat_enabled` / `group_chat_enabled` / `routing_enabled` behave the same. |
| `chief_of_staff.alerts_enabled` | `SYNTHORG_CHIEF_OF_STAFF_ALERTS_ENABLED` | Off-by-default autonomous capability (also: `learning_enabled` / `narrative_enabled` / `invite_enabled`). No restart: `alerts_enabled` is started/stopped live by `ChiefOfStaffAlertsSettingsSubscriber`; the others are gated per cycle/turn. Each additionally requires the persona master switch `self_improvement.chief_of_staff_enabled`. |
| `chief_of_staff.direct_mcp_enabled` | `SYNTHORG_CHIEF_OF_STAFF_DIRECT_MCP_ENABLED` | Off-by-default autonomous MCP acting; hot-reloadable, still fail-closed. The subsystem reconciler rebuilds the actor through the same fail-closed builder on toggle, so the actor materialises only when security governance + the MCP self-consumer are wired on the boot engine (else it stays inert and the endpoint 503s). The fail-closed property moved from a restart bind to a per-rebuild governance re-check, so it no longer needs a restart. |
| `chief_of_staff.chat_model` | `SYNTHORG_CHIEF_OF_STAFF_CHAT_MODEL` | Per-feature model for conversational turns (also `propose_model` / `routing_model` / `narrative_model`); read live per LLM call, no restart. Auto-filled at setup-complete when left blank. |
| `knowledge.enabled` | `SYNTHORG_KNOWLEDGE_ENABLED` | On-by-default knowledge substrate; ghost-wired at boot and live-gated per request at the knowledge tools (no restart). The `knowledge.synthesis_model` / `knowledge.synthesis_provider` / `knowledge.synthesis_synthesizer` / `knowledge.synthesis_max_chunks` keys rebuild + swap the synthesiser via a subscriber; `knowledge.synthesis_enabled` remains live-gated at the entrypoint. |
| `research.enabled` | `SYNTHORG_RESEARCH_ENABLED` | On-by-default research pipeline; ghost-wired at boot and live-gated per request at the research tools (no restart). The model lives in `research.model` (auto-filled at setup-complete); model / provider / strategy / threshold keys rebuild + swap the service via a subscriber. |
| `self_improvement.enabled` | `SYNTHORG_SELF_IMPROVEMENT_ENABLED` | Off-by-default self-modification master switch; read live per cycle by `run_cycle` (with `engine.evolution_enabled`), so toggling it applies with no restart. The strategy toggles (`config_tuning_enabled` / `architecture_proposals_enabled` / `prompt_tuning_enabled`), `tool_creation_enabled` (+ its allowlist), and the analysis / code-mod models are likewise live. |
| `self_improvement.code_modification_enabled` | `SYNTHORG_SELF_IMPROVEMENT_CODE_MODIFICATION_ENABLED` | Off-by-default self-modifying code; read live per cycle through the feature overlay. It additionally requires GitHub credentials in the `meta.self_improvement` blob: without them the next config load refuses and forces the flag back off, which surfaces the failure to an operator rather than leaving a silently un-applied switch. |
| `providers.tool_call_feedback_enabled` | `SYNTHORG_PROVIDERS_TOOL_CALL_FEEDBACK_ENABLED` | Master switch for the runtime tool-call failure feedback loop (default `true`). Re-read live per observation by the `ToolCallFeedbackTracker`, so toggling it on/off applies without a restart while the sink stays installed. |
| `providers.tool_call_failure_threshold` | `SYNTHORG_PROVIDERS_TOOL_CALL_FAILURE_THRESHOLD` | Decayed-score threshold (1..20, default 3) at which a model is downgraded (`tool_calls_verified=False`). Re-read on each failure. |
| `providers.tool_call_failure_decay_half_life_seconds` | `SYNTHORG_PROVIDERS_TOOL_CALL_FAILURE_DECAY_HALF_LIFE_SECONDS` | Half-life (60s..86400s, default 3600s) over which a failure's weight halves, so a transient blip decays away rather than permanently downgrading a capable model. Re-read on each failure. |

### Category 2 examples (env > default; DB bypassed)

| Setting | Env override | Notes |
|---|---|---|
| `api.server_host` | `SYNTHORG_API_SERVER_HOST` | Consumed pre-init via `bootstrap_resolver` at app construction; registry entry for `/settings` discoverability. |
| `api.server_port` | `SYNTHORG_API_SERVER_PORT` | Same as above. |
| `api.api_prefix` | `SYNTHORG_API_API_PREFIX` | Same. |
| `api.cors_allowed_origins` | `SYNTHORG_API_CORS_ALLOWED_ORIGINS` | Same. JSON-encoded list. |
| `api.trusted_proxies` | `SYNTHORG_API_TRUSTED_PROXIES` | Same. JSON-encoded list. |
| `communication.nats_url` | `SYNTHORG_NATS_URL` | Read once by the bus driver at startup. |
| `workers.count` | `SYNTHORG_WORKERS` | A launch argument of the separate worker process, passed by `synthorg worker start`. |
| `observability.log_directory` | `SYNTHORG_LOG_DIR` | Path-traversal validated at the boot site. |
| `observability.tsa_endpoint_freetsa` | `SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_FREETSA` | Timestamp-authority trust anchor, resolved during `configure_logging` before the DB-backed resolver exists. |

### Category 3 examples (env only; no registry entry)

| Concern | Env var | Boot site |
|---|---|---|
| SQLite path | `SYNTHORG_DB_PATH` | `api/boot_persistence.py`, `api/app_helpers.py`, `api/integrations_wiring.py` |
| Postgres URL | `SYNTHORG_DATABASE_URL` | `api/boot_persistence.py`, `api/app_helpers.py` |
| Postgres SSL mode | `SYNTHORG_POSTGRES_SSL_MODE` | `api/boot_persistence.py` |
| Config-file path | `SYNTHORG_CONFIG_PATH` | `api/boot_persistence.py`, `backup/factory.py` |
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
that exact name instead. Settings using overrides:

| Registry key | Override env var |
|---|---|
| `observability/log_directory` | `SYNTHORG_LOG_DIR` |
| `observability/log_level_console` | `SYNTHORG_LOG_LEVEL` |
| `communication/nats_url` | `SYNTHORG_NATS_URL` |
| `workers/count` | `SYNTHORG_WORKERS` |
| `workers/executor_http_timeout_seconds` | `SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS` |
| `tools/sandbox_image` | `SYNTHORG_SANDBOX_IMAGE` |
| `tools/sidecar_image` | `SYNTHORG_SIDECAR_IMAGE` |
| `memory/fine_tune_image` | `SYNTHORG_FINE_TUNE_IMAGE` |
| `memory/fine_tune_data_volume` | `SYNTHORG_FINE_TUNE_DATA_VOLUME` |
| `integrations/tunnel_state_dir` | `SYNTHORG_TUNNEL_STATE_DIR` |

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
3. **Category 2 (fixed by the deployment but operator-visible):**
   register with `compose_set=True`, and add the env var in the same
   change to **both** backend compose sources
   (`cli/internal/compose/compose.yml.tmpl` and `docker/compose.yml`),
   or to `cli/cmd/worker_start.go` for a setting only the worker reads:
   the `SettingsService` rejects runtime mutation and bypasses the DB
   on reads, and `check_setting_compose_backed.py` fails a claim the
   deployment does not actually back.
4. **Category 3 (bootstrap secret):** do **not** register. Read the
   env var directly at the boot site and document the env var on
   [environment-variables.md](environment-variables.md). Capture into
   a typed domain object; never re-read.
5. Consume the value via `ConfigResolver.get_*()` (post-init) or
   `synthorg.settings.bootstrap_resolver.resolve_init_value(...)`
   (pre-init). Direct `os.environ.get` reads in application code
   outside startup are forbidden. For a Category-1 setting this step is
   also what satisfies `check_setting_live_or_compose_set.py`: a read
   that runs only while the runtime is assembled does not count, so see
   [The complement is enforced too](#the-complement-is-enforced-too)
   before wiring one into worker assembly.

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
from synthorg.settings.enums import SettingNamespace

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

### Available parsers

`synthorg.settings.mirrors` ships the parser callbacks below. A
`MirrorField` with `parse=None` applies identity parsing (the raw env
string reaches the field, and the Pydantic field type does any
coercion). A parser returning `None` signals invalid input; the
registered default is then applied.

| Parser | Signature | Use for |
|---|---|---|
| `parse_bool` | `(str) -> bool \| None` | Boolean tokens (`true`/`false`/`1`/`0`/`yes`/`no`). |
| `parse_int` | `(str) -> int \| None` | Integer settings. |
| `parse_float` | `(str) -> float \| None` | Float settings. |
| `parse_str_tuple_json` | `(str) -> tuple[str, ...] \| None` | JSON list-of-strings into a tuple. |
| `parse_json_int_pair_dict` | `(str) -> dict[str, list[int]] \| None` | JSON `{op: [int, int]}` (e.g. `PerOpRateLimitConfig.overrides`). Top-level shape only; the owning config's `mode="before"` validator promotes inner lists to tuples and rejects negatives. |
| `parse_json_int_dict` | `(str) -> dict[str, int] \| None` | JSON `{op: int}` (e.g. `PerOpConcurrencyConfig.overrides`). Top-level shape only; the owning validator rejects non-int / negative values. |

The two JSON-dict parsers deliberately validate only the top-level
JSON structure. Per-entry semantics (non-blank keys, tuple arity,
non-negativity) belong to the owning config's `mode="before"`
validator so operator-facing error context fires before Pydantic
coercion. See "Validator declaration order" in
[conventions.md](conventions.md).

### Sentinel-preserving mode: `only_if_env_set=True`

When the Pydantic field's `None` default carries semantic meaning the
registry default would clobber, set `only_if_env_set=True` on the
`MirrorField`. The mirror then fires ONLY when the operator has
explicitly set the env var; if the resolver falls back to the
registered default the Pydantic field keeps its declared default.
Used by:

- `AuthConfig.exclude_paths` (`None` = auto-derive from API prefix)
- `CoordinationSectionConfig.max_concurrency_per_wave` (`None` = unlimited)

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

## A settings write that swaps a collaborator must swap a complete one

The precedence chain gets the *value* right; the swap has to get the
*collaborator* right. A boot-time model-refresh sweep found three stale models,
rewrote `providers.configs`, and swapped in a rebuilt `ProviderRegistry` that
`_build_registry_and_router` had constructed with no `connection_catalog`. The
value was correct and the swap was silent, and every dispatch after it went out
with no credential.

So the swap seam owns the invariant, not each caller:
`AppState.swap_provider_registry` rebinds the always-on credential catalog onto
the incoming registry before installing it, and the worker's
`_select_active_provider` reads the registry once and threads that instance
through the rest of the build so a mid-build swap cannot split the runtime
across two registries. The rule generalises: **whatever a settings write
replaces, it replaces whole**, and the seam that installs it is where that is
enforced. See [security.md](../security.md) for the fail-closed half.

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
3. **`BridgeConfigState` owner + accessors.** The
   `app_state.bridge_config` owner default-constructs each bridge
   model so consumers always see a valid snapshot, even before
   `_apply_bridge_config` has run. `app_state.bridge_config.<name>`
   returns the current snapshot; `app_state.bridge_config.swap_<name>(config)`
   does a wholesale replace under a per-bridge `threading.Lock`;
   `app_state.bridge_config.mutate_<name>({field: value, ...})`
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

Use this pattern when per-request resolver lookup would add overhead or
coupling. Where a single live object owns the value (e.g.
`ws_auth_timeout_seconds`), the simpler `set_*()` pattern in
`_apply_bridge_config` plus a subscriber is sufficient.

## Bootstrap-wiring trace (ghost-wired settings gate)

A registered setting whose consuming machinery exists but is never instantiated at boot is **ghost-wired**: the value resolves cleanly through the precedence chain, but no code path that reads it ever runs in default config. The standing gate `scripts/check_setting_to_startup_trace.py` (pre-push and CI) detects two ghost-service patterns and matches settings to them via three matchers. The full mechanics, the suppression marker, and the baseline-file contract have their own reference: [Bootstrap-Wiring Trace (Ghost-Wired Settings Gate)](bootstrap-wiring-trace.md).

## Compose-set or live

A **registered** setting is either fixed when a process starts or changeable
while the system runs. There is no third category, and therefore no restart
control and no pending-restart state: a `compose_set=True` definition rejects a
write and is shown read-only in the dashboard, and everything else applies
immediately through one of the seams below.

This is a statement about the settings registry, not about every value the
process reads. The Category 3 bootstrap secrets above are pure environment and
are never registered: they are consumed before a settings backend exists, so
they have no definition to carry the flag and no write path to reject.

`compose_set` is reserved for what a running process genuinely cannot change
about itself: the bind address and TLS material uvicorn already opened, the
sandbox image the CLI pulled and verified, the RFC 3161 trust anchors resolved
before the settings backend exists, the Litestar middleware exclusion lists
applied at mount time. `scripts/check_setting_compose_backed.py` (pre-push +
CI) requires the shipped tooling to actually pass each one's environment
variable, so the label cannot decay into "we did not wire this up": a backend
`compose_set` key missing from either `cli/internal/compose/compose.yml.tmpl`
or `docker/compose.yml`, or a worker-only one missing from
`cli/cmd/worker_start.go`, fails the gate.

Everything else is live, through whichever seam its consumer allows:

- a per-request / per-call `ConfigResolver.get_*()` read (e.g.
  `api.max_meeting_context_keys`, `api.readiness_probe_timeout_seconds`,
  `integrations.oauth_http_timeout_seconds`, and the `charter.interview_*` /
  `charter.default_currency` knobs the interview service resolves once per
  turn through an injected config provider);
- a `set_*()` setter on a live object pushed by a `SettingsSubscriber`
  (e.g. the `WsAuthLimits` knobs, the `HttpBatchHandler` HTTP batch knobs,
  `backup.path`, the `backup.compression` / `on_shutdown` / `on_startup`
  config flags, `a2a.client_timeout_seconds`,
  `engine.timeout_enforcement_enabled`,
  `observability.audit_chain_signing_timeout_seconds` re-applied onto the
  live audit sink, and `integrations.github_api_url` re-bound onto the
  GitHub health checker);
- a bridge-config snapshot + subscriber (e.g. the `tools.docker_sidecar_*`
  resource limits, read per container launch through the sidecar cache);
- a rebuild-and-swap subscriber that re-resolves and replaces a live
  strategy / pipeline (the `hr.eval_loop_llm_model` /
  `pattern_identifier_mode` / `fix_proposer_mode` strategies swapped onto
  the eval-loop coordinator);
- a `reload_runtime_services` trigger for knobs `build_runtime_services`
  already re-reads (the engine classifier / matcher knobs,
  `external_api.enabled` / `provider_type`,
  `coordination.enable_coordination_middleware`,
  `budget.benchmark_provider` / `model_capability_overrides`, and the
  `simulations.verification_review_enabled` / `verification_grader` /
  `verification_decomposer` pipeline rebuild). These writes arrive in bursts,
  because saving a settings form writes one key per field, so the dispatcher
  batches them over `settings.dispatcher_coalesce_window_seconds` and hands the
  subscriber one batch to serve with one rebuild. The window is the dispatcher's
  and applies to every subscriber, not a knob of this one. What a write promises
  is unchanged: it returns only after a rebuild that started after it, and a
  failed rebuild still raises to every write it carried. The window is the added
  latency on a single write, and 0 turns the wait off;
- a `settings=` declaration on a `SubsystemSpec`, which puts the key in the
  watched set so a write to it triggers a reconcile pass. That alone does not
  replace a running subsystem: `rebuild_on_change=True` is what makes the pass
  tear the subsystem down and activate it again. `memory_backend` declares
  both, which is why `memory.embedder_model` and its siblings reconnect the
  memory backend on the spot. See
  [Subsystem Reconciliation](../design/subsystem-reconciliation.md).

### The complement is enforced too

`scripts/check_setting_live_or_compose_set.py` (pre-push + CI) is the sibling
of the compose-backed gate and asks the opposite question of every writable
setting: can an operator's write reach anything that is running? A setting that
nothing reaches accepts the write, shows the new value on the settings page, and
changes no behaviour. The value is not lost: it is retained and picked up the
next time the runtime is rebuilt. But nothing about the write schedules that
rebuild, so it arrives only as a side effect of some unrelated watched key
firing, or of a restart. An operator therefore cannot tell from the dashboard
when, or whether, the change has taken effect, which is the third category the
rule abolishes wearing the first category's clothes.

The gate accepts as evidence any of the seams above, read straight from the
source tree rather than by importing it:

- a `(namespace, key)` pair in any settings subscriber's `_WATCHED` set;
- an `enabled_by` entry on a `SubsystemSpec`, or a `settings=` entry on a spec
  that also declares `rebuild_on_change=True` (see below);
- a resolver read in any shape the tree uses: a positional `(ns, key)` pair,
  `namespace=` / `key=` keywords (which is also what a `MirrorField`
  declaration looks like), a `_resolve_bridge_fields` bundle, a namespace-wide
  `get_namespace` / `get_page` / `get_all` read, a dotted `"ns.key"` literal, a
  loop over a literal collection of keys, or a helper that takes the namespace
  or key as a parameter and is called with a literal;
- the namespace *and* the key quoted in the same `web/src/` file, outside test
  files and generated `*.gen.ts` types. The dashboard persists no domain state
  and re-fetches through `GET /settings`, so a key it reads applies on the next
  render. Both halves are required because eight settings share the key
  `enabled`, and matching the key alone would let one unrelated token certify
  every one of them.

Two things are deliberately *not* evidence. A read inside any module
`build_runtime_services` reaches runs while the runtime is assembled, and a
read inside a function a `SubsystemSpec` names as its `activate=` /
`deactivate=` target runs during activation; both happen once per rebuild. The
construction path is derived by closing over the `synthorg.workers` imports of
the module defining `build_runtime_services`, rather than listed, so it does
not go stale as the assembly grows.

Declaring the key in that subsystem's `settings=` makes the read live again
**only alongside `rebuild_on_change=True`**. Without the flag the reconciler
short-circuits on an already-active subsystem, so the write is watched but
replaces nothing and the value waits for the next fresh wiring: watched is not
the same as applied. `enabled_by` needs no flag, because the reconciler
evaluates it on every pass regardless.

The activation analysis follows one import hop (the registry's `_activate_*`
wrapper to the wiring function it calls) and excludes reads lexically inside
that function. It matches the function by name rather than tracing calls, so a
read inside a helper that wiring function calls is not excluded and stays
live.

#### A blank-default setting is judged on cold-start evidence

A setting that ships with no value at all (`default=""`, `default=None`, or no
`default=`) is in a position the seams above do not describe. For it the write
that matters is the *first* one: the one that turns the feature on. Two shapes
that satisfy the general rule prove nothing about that write, so the gate does
not credit them here.

- **A read that supplies a fallback.** It lives inside the component the
  setting decides whether to build, and falls back to the pair that component
  was built with. It lets an operator move a running instance to a different
  model; it cannot bring one into being, because it does not run until one
  exists.
- **A namespace-wide bulk read.** It names the namespace, not the key: it
  sweeps up every entry including the ones nothing consumes, and in this tree
  it feeds a boot config snapshot rather than a live consumer.

Between them those two masked `chief_of_staff.turn_intent_model`, which was
written, persisted, shown in the dashboard, and applied to nothing until a
restart, while the gate passed it on a live read inside the classifier the
setting itself decided whether to build. What still counts for a blank-default
setting is a declaration (subscriber pair, `enabled_by`, rebuild-backed
`settings=`, dashboard reference) or a read with nowhere to fall back to: the
`resolve_bound_model_live` + `require_configured_model` shape, where unset
fails loud with a 503 naming the setting, so the first write arms the very next
call. The violation is reported as kind `gated-by-itself`, and its message
names the seams that would fix it rather than only the verdict.

There is no per-line opt-out. A marker on this rule would read "this setting is
writable and reaches nothing, and that is fine", which is the category the rule
exists to abolish. The three sanctioned exits are: make it live, mark it
`compose_set` and pass it from the launchers (which the sibling gate then
checks), or delete it. Pre-existing violations are frozen in
`scripts/setting_live_or_compose_set_baseline.txt`, one
`<namespace>.<key>:<kind>` per line, where `kind` is `unreachable` (nothing
names it), `construction-only` (the only reads run while the runtime is
assembled), or `gated-by-itself` (blank by default, and its only reads sit
inside the component it gates); a listed setting whose kind changes is a new
violation, not a covered one.

### Security toggle write guardrail

`security.enabled`, `audit_enabled`, `post_tool_scanning_enabled`, and
`output_scan_policy_type` are hot-reloadable, but **weakening** them is a
deliberate-action decision: turning a boolean off, or switching
`output_scan_policy_type` to `log_only`, requires `confirm=True` plus a
non-blank `reason` and actor at the write path
(`settings/write_governance.py`, enforced centrally in
`SettingsService.set` / `set_many`, surfaced via the dedicated
`POST /settings/security/import` endpoint). Enabling / tightening applies
immediately with no gate. The per-request interceptor reads the live config
through `app_state.security_runtime_config`, which the
`SecurityBridgeSettingsSubscriber` swaps on an authorised change.

The same guardrail covers four more namespaces: `engine` (the completion-oracle
keys, the agent middleware, the three loop-routing keys
`loop_auto_select_enabled`, `default_loop_type` and `loop_complexity_overrides`,
where naming the sandboxed loop is the weakening direction, and the three
human-ask toggles `ask_policy_enabled`, `clarification_enabled` and
`scoping_enabled`, whose off direction removes the only in-run path by which an
agent defers a material, hard-to-reverse choice to a human), `tools` (MCP
sandbox isolation, the credentialed-MCP grant, `openhands_enabled`, and each
destructive tool family's enable + targets), `output_style` (disable, shadow,
exemptions, pack swap), and `providers` (`gateway_enabled`,
`failover_enabled`, `failover_routes`).

Six of those toggles ship **on**, and the guarded direction differs by what
turning one on actually does.

For the three **default-on capabilities** (`providers.gateway_enabled`,
`tools.openhands_enabled`, `tools.credentialed_mcp_enabled`) the weakening
direction is `false` -> `true`, because that is what reopens an egress or
credential surface. An unset key already resolves to the registered `true`, so
writing `true` over it restates the running posture and is unguarded; only an
explicit stored `false` returning to `true` needs confirm+reason+actor.

For the three **human-ask toggles** (`ask_policy_enabled`,
`clarification_enabled`, `scoping_enabled`) it is the mirror image: turning one
off is what removes the deferral path, so `true` -> `false` and `unset` ->
`false` need confirm+reason+actor (unset counts because it resolves to the
registered `true`), while `false` -> `true` restores the posture and is
unguarded.

Either way the guardrail is a live-write control, not an upgrade-time one: a
deployment that never wrote an explicit row inherits the new default on its
next boot with no prompt, so a default flip on any of these belongs in the
release notes.

Seven `tools` keys this guardrail covers are also frozen `construction-only` in
`scripts/setting_live_or_compose_set_baseline.txt`: `mcp_sandbox_enabled`,
`mcp_sandbox_network`, `mcp_sandbox_cpus`, `mcp_sandbox_memory_limit`,
`mcp_sandbox_pids_limit`, `forge_tools_enabled` and `chat_tools_enabled`. The
combination is worth naming: an operator completes the confirm + reason + actor
prompt, the write is accepted and the dashboard shows the new value, and the
sandbox isolation posture stays as it was until the next runtime rebuild. For
these the gap is a security-posture one rather than a convenience one, so they
are the priority set for the follow-up that makes each key live, ahead of the
timeout and limit entries sitting beside them in the baseline.

The two **declared-failover** keys ship **off**, so their guarded direction is
the plain one and unset counts as off. `providers.failover_enabled` needs
confirm + reason + actor on the first stored `true`, because that is what
widens what may answer a bound request: the same model id through two
connections is two different calls, billed and rate-limited separately.
`providers.failover_routes` is guarded on **addition** rather than on the
value as a whole, keyed `declared -> alternate`, so declaring a first route,
adding a second, and repointing an existing pair at a different connection
each need the guardrail, while removing one, clearing them all, or reordering
the same set is a narrowing and is not guarded. Keying on both halves is what
makes the repoint case guarded: the declared half is unchanged and the count
is unchanged, but a connection that could not serve that pair now can, and the
enable toggle cannot ask again about a grant made months later.

`integrations.webhook_receipt_retention_days` is governed for a different reason:
it relaxes no boundary, but **shortening** the window has the next sweep destroy
delivery evidence irreversibly, so the shortening direction (including the
default never-sweep `0` becoming any finite window) needs the same
confirm + reason + actor. Lengthening it, or returning to `0`, retains strictly
more and is unguarded.

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
`notifications.dispatcher.NotificationDispatcher.dispatch`.

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
