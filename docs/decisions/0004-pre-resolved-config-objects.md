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

1. `MemoryConfig` for `MemoryService`: consolidation interval,
   `max_memories_per_agent`, retention/archival toggles resolved into
   one frozen model; the service stops holding a resolver for these.
2. `OptionalSettingsGate` and its callers: the gate resolves once into
   a frozen snapshot.
3. Full sweep: every service constructed with `config_resolver=` that
   performs per-call `ConfigResolver.get_*`. The implementation
   inventory is built by enumerating `config_resolver.get_` across
   `src/synthorg/` and grouping by owning service / namespace. Each
   group gets one `<Ns>BridgeConfig` frozen model, a
   `ConfigResolver.get_<ns>_bridge_config()` builder, `AppState`
   slot + accessors, and a `settings/subscribers/<ns>_bridge_subscriber.py`.

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

### Phased plan (within this PR)

1. `MemoryService` / `MemoryConfig` first (reference conversion).
2. `OptionalSettingsGate`.
3. Remaining namespaces, one commit per namespace bridge, each
   verified green by the ghost-wired
   (`check_setting_to_startup_trace.py`) and full test suites before
   the next.

## Migration mechanics

Per namespace group:

1. Add `<Ns>BridgeConfig(BaseModel)` with
   `ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")`, one
   field per consumed setting, defaults matching the registry.
2. Add `ConfigResolver.get_<ns>_bridge_config()` resolving all fields
   via the shared `_resolve_bridge_fields()` helper.
3. Add the `AppState` slot, `<name>_bridge_config` accessor,
   `swap_*` / `mutate_*` under a per-bridge lock; default-construct in
   `AppState.__init__` so consumers see a valid snapshot pre-startup.
4. Add `settings/subscribers/<ns>_bridge_subscriber.py` with the
   `_WATCHED` set + module-load existence guard; register it where
   `SettingsService` subscribers are wired.
5. Change the service constructor to take the frozen config (or read
   `AppState.<name>_bridge_config`); delete the `config_resolver` field
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
