# ADR-0004: Pre-resolved config objects

## Status

Accepted, implemented in WP-4 (issue #1919).

## Context

Many services are constructed with a `config_resolver: ConfigResolver`
and call `await config_resolver.get_bool(ns, key)` /
`get_int` / `get_float` on the hot path, once per request or per loop
iteration. This couples every service body to the settings resolution
machinery, repeats the (namespace, key) string pair at each callsite,
re-resolves values that change rarely, and makes the service's
configuration surface invisible at construction (a reader must grep
`config_resolver.get_*` calls to learn what knobs a service reads).

The codebase already has the canonical fix for one case:
`api.max_lifecycle_events_per_query` is consumed by
`ActivityController` via the bridge-config pattern described in
`docs/reference/configuration-precedence.md`: a frozen Pydantic
snapshot on `AppState`, a `ConfigResolver.get_<ns>_bridge_config()`
builder, `AppState` accessors with a per-bridge `threading.Lock`, and a
`SettingsSubscriber` that hot-swaps the snapshot on operator change.
The pattern exists but is applied to exactly one namespace.

## Decision

Every config-dependent service takes a frozen pre-resolved config
object constructed once, instead of holding a `ConfigResolver` and
resolving per call. The bridge-config machinery in
`settings/bridge_configs.py` + `settings/subscribers/` is the
mechanism, generalised namespace-by-namespace. Hot-reloadable knobs
stay hot-reloadable through the subscriber's `mutate_*`; the service
body reads a plain frozen field.

This PR performs the full sweep (big-bang), not a single pilot:

### Scope

1. `MemoryBridgeConfig` (the memory namespace): the consolidation
   enforce-batch size and the embedding fine-tune preflight knobs
   (`fine_tune_vram_batch_table`, `fine_tune_chunk_size`) resolved into
   one frozen model held by the `app_state.bridge_config` owner, with a
   startup snapshot and a hot-swap subscriber. The live consumer -- the
   fine-tune preflight batch-size recommendation in
   `api/controllers/memory.py` -- reads
   `app_state.bridge_config.memory.fine_tune_vram_batch_table` per
   request instead of a module constant. The `consolidation_enabled`
   master kill-switch stays a per-cycle resolve (the deliberate
   kill-switch idiom, unchanged). The remaining two fields'
   consumers -- `MemoryConsolidationService` and `FineTuneOrchestrator`
   -- are not constructed in production today; constructing them into
   startup (which activates those fields) is the enumerated next phase
   below, not a silent gap.
2. Cluster-2 carry-over knobs whose consumers still read a per-call
   resolver: `TaskExecutionExecutor.executor_http_timeout` and
   `CoordinationMetricsStore` (`budget.coordination_metrics_max_entries`).
   (The plan's `OptionalSettingsGate` item is dropped: no such class or
   concept exists in `src/synthorg/`; it was an aspirational plan
   reference, verified absent during implementation.)
3. Full sweep: the inventory was built by enumerating
   `config_resolver.get_*` across `src/synthorg/` and classifying each
   call site. The outcome is **not** "every site becomes a frozen
   bridge" -- it is "every config-dependent service uses the canonical
   mechanism appropriate to its tier":

   - **Frozen `<Ns>BridgeConfig` on the `app_state.bridge_config`
     owner + hot-swap subscriber** (the new pattern): `api`, `workers`,
     `memory`.
   - **Resolve-once `set_config_resolver` at boot** (cluster-2,
     task #2, already shipped): `OAuthTokenManager`,
     `WebhookEventBridge`, `MessageBusBridge`, `JetStreamMessageBus`
     history params, the escalation notifiers. These are built before
     `AppState`; the setter + start-time resolve is their canonical
     form (mirrored by the dispatcher's late-bound provider).
   - **Cat-2 boot knob** (`resolve_init_value`, env > default): the
     worker subprocess `executor_http_timeout_seconds` and the
     `CoordinationMetricsStore` ring-buffer cap -- consumers with no
     `SettingsService` in scope.
   - **Deliberately per-call, preserved by invariant** (NOT swept):
     loop kill-switches (`*_enabled` gates whose fail-open semantics
     ADR-0004's first invariant protects), the company-snapshot ETag
     live reads in `org_mutations` (a frozen snapshot would make the
     ETag stale by construction), the `health_prober` per-cycle
     provider+port reads (the loop re-reads each cycle -- that IS the
     hot-reload mechanism), and the agent-engine personality
     fail-open per-prompt reads. Forcing these into frozen snapshots
     would regress documented behaviour, not improve it.

   Conditionally-wired settings whose only consumer is an opt-in
   subsystem (the distributed dispatcher behind `queue.enabled` +
   `synthorg[distributed]`) carry the gate's sanctioned
   `# lint-allow: bootstrap-wiring` marker rather than a fake
   unconditional start.

### Invariants preserved

- **Fail-safe-to-enabled**: a settings-backend outage must not silence
  a loop. Each bridge model's field default equals the registered
  setting default, so a resolver failure falls back to the safe value,
  not to a silenced surface (the kill-switch idiom in
  configuration-precedence.md is unchanged).
- **Module-load guard**: every key in a subscriber's `_WATCHED` set is
  asserted to exist on its bridge model at import, so a typo or rename
  fails at startup, not on the next operator hot-reload.
- **Lock discipline**: `swap_*` / `mutate_*` hold a per-bridge
  `threading.Lock`; `mutate_*` re-validates the merged dict through
  `model_validate` so an out-of-range hot value raises and the prior
  snapshot is retained.
- **Restart-required knobs** use the simpler boot-time `set_*` pattern
  in `_apply_bridge_config`, not a subscriber.

### Phased plan

Within this PR:

1. `WorkersBridgeConfig` (dispatcher publish-retry budget) -- full
   chain incl. live `DistributedDispatcher` consumer via a late-bound
   provider (reference conversion).
2. `MemoryBridgeConfig` -- full infra chain + the live fine-tune
   preflight VRAM-table consumer.
3. Cluster-2 carry-over: `TaskExecutionExecutor.executor_http_timeout`
   and `CoordinationMetricsStore` enforce-bound.
4. Remaining namespaces, one commit per namespace bridge, each
   verified green by the ghost-wired
   (`check_setting_to_startup_trace.py`) and full test suites before
   the next.

Subsequent (consumer construction, separately tracked): wire
`MemoryConsolidationService` and `FineTuneOrchestrator` into production
startup so the `consolidation_enforce_batch_size` and
`fine_tune_chunk_size` bridge fields gain live consumers. The bridge
infra landing first means that work is pure construction -- no further
settings plumbing.

## Migration mechanics

Per namespace group:

1. Add `<Ns>BridgeConfig(BaseModel)` with
   `ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")`, one
   field per consumed setting, defaults matching the registry.
2. Add `ConfigResolver.get_<ns>_bridge_config()` resolving all fields
   via the shared `_resolve_bridge_fields()` helper.
3. Add the snapshot field to the `app_state.bridge_config` owner
   (`BridgeConfigState`): the `<name>` accessor and `swap_<name>` /
   `mutate_<name>` under a per-bridge lock; default-construct it in
   `BridgeConfigState.__init__` so consumers see a valid snapshot
   pre-startup.
4. Add `settings/subscribers/<ns>_bridge_subscriber.py` with the
   `_WATCHED` set + module-load existence guard; register it where
   `SettingsService` subscribers are wired.
5. Change the service constructor to take the frozen config (or read
   `app_state.bridge_config.<name>`); delete the `config_resolver` field
   where it is now unused; update all callsites and tests.
6. Tests per bridge: valid default snapshot before `_apply_bridge_config`
   runs; hot-reload `mutate` applies; out-of-range `mutate` rejected
   and prior snapshot retained; concurrent `mutate` does not lose a
   write.

## Compat scope

None. A service either takes the resolver or the frozen config, not
both. The `config_resolver` parameter is removed from each converted
service in the same commit that introduces its bridge config; no
service keeps a dual constructor.

## Alternatives considered

- **Phased pilot (MemoryService only this PR, rest later).** Rejected
  (user decision): the resolver-per-call pattern is the exact "scattered
  plumbing" this work package exists to remove; a single pilot leaves
  the codebase in two states across many namespaces for an extended
  period.
- **Resolve once in `__init__` into plain attributes, no subscriber.**
  Rejected: loses runtime hot-reload for `restart_required=False`
  knobs, a capability operators currently have via `ConfigResolver`.
- **Keep `ConfigResolver` per call.** Rejected: status quo; the
  coupling, repetition, and invisible config surface are the
  motivation.

## Consequences

- Many service constructors change signature in one PR; commits are
  one-per-namespace so review is tractable.
- Every converted service's configuration surface is now legible at
  construction (the bridge model is the manifest).
- New bridge subscribers increase the `SettingsService` subscriber
  count; the module-load guard makes a stale `_WATCHED` key a startup
  failure.
- Out of scope: registering new settings (RFC-unrelated; see the
  WP-4 settings-bridging deliverable), web / CLI.
