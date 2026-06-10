# ADR-0007: Feature-manifest substrate + composition-root strategy

## Status

Accepted. Substrate landed in EPIC #2046 PR 2 (issue #2048); per-feature
manifests + enforcement gates landed in issue #2149 (this PR).

## Context

SynthOrg's pre-#2048 composition root was a 2313-line `api/state.py`
god-object plus a 2152-line `api/app.py` that wired every service. Adding
a feature required touching both centres; the side-effect was that the
"locality" the project espoused at the feature level evaporated as soon
as the wiring met the centre.

ADR-0006 (the tiered module-size policy) gated NEW central growth but
could not, by itself, undo the centre. What the codebase needed was an
actual seam where a feature could declare its whole surface: settings
namespace, state slice, REST controllers, MCP tool contributions,
lifecycle hooks, boot-constructed ("ghost-wired") symbols, and the
features it depends on. The seam had to satisfy four constraints:

1. **One file per feature**. An AI agent (or human) opens the feature's
   `feature.py` and learns everything about the feature.
2. **Typed**. The substrate is Pydantic; a typo in a manifest field
   fails at import, not at runtime. State slices are frozen so a
   hot-reload composes a new slice and swaps atomically.
3. **Discoverable**. Boot walks the filesystem for `feature.py` files,
   imports each, resolves the dependency graph, and composes the
   slices. No central registry.
4. **Enforced**. Adding a new feature without a `feature.py` fails at
   pre-push. Growing `AppState.__slots__` past the approved set fails.
   The AI-navigation index `data/feature_index.json` stays in sync with
   the manifests.

## Decision

### The substrate (shipped in #2048)

`src/synthorg/_core/features.py` exposes:

- `BaseFeatureStateSlice`: frozen Pydantic base for per-feature state
  slices (`frozen=True, extra="forbid", arbitrary_types_allowed=True`).
- `FeatureModule` (Protocol) + `FeatureManifest` (frozen concrete model).
  Manifest fields: `name`, `settings_namespace`, `state_slice`,
  `controllers`, `mcp_handlers`, `lifecycle_hooks`,
  `ghost_wired_symbols`, `depends_on`.
- `McpHandlerDescriptor`: frozen descriptor a feature attaches under
  `mcp_handlers` to declare its MCP domain + tool names.
- `LifecycleHook`, `McpHandlerModule`: read-only Protocols a feature
  satisfies structurally.
- `discover_features()`: filesystem walk + import + dependency-order
  resolution. Memo-cached at module level; tests pass `force=True` to
  rebuild.
- `feature_directories()`: maps feature name -> repo-relative directory.
  Consumed by the navigation-index generator.
- `resolve_feature_order()`: pure topological sort with deterministic
  tiebreak (lexicographic). Cycles, duplicates, and unknown
  dependencies raise `FeatureDependencyError`.

`src/synthorg/api/state_slices.py` ships `AppStateSliceMixin` with the
typed slice store (`slice`, `set_slice`, `swap_slice`, `wire`,
`set_field_once`, `wire_if_field_absent`, `swap_field_returning_previous`),
preserving the once-only / if-absent / hot-replace contracts the legacy
seams provided. `compose_feature_slices(app_state)` composes an empty
slice for every discovered feature at boot.

### Per-feature manifests (shipped in #2149)

Every directory under `src/synthorg/` whose `state.py` (or peer
`*_state.py` for the api-core case) declares a `BaseFeatureStateSlice`
subclass carries a sibling `feature.py` exposing a module-level
`FEATURE: FeatureModule`. The live tree carries 32 feature manifests,
most under top-level packages (`a2a`, `api_core`, `approval`, `backup`,
`budget`, `client`, `communication`, `coordination`, `docs`, `engine`,
`facades`, `hr`, `integrations`, `knowledge`, `memory`, `meta`,
`notifications`, `observability`, `ontology`, `organization`,
`persistence`, `providers`, `research`, `runtime`, `security`,
`settings`, `telemetry`, `tools`) and the rest nested under those
parents (`meta/charter`, `meta/toolsmith`, `engine/cockpit`,
`engine/workspace`).

Each `feature.py` carries `# module-kind: feature` on the first
non-blank line so the module-size budget gate caps it at 100 LOC.

### Three new enforcement gates (shipped in #2149)

1. `scripts/check_feature_manifest.py` walks `src/synthorg/**/state.py`
   for `BaseFeatureStateSlice` subclasses; every such directory must
   ship a `feature.py` carrying `# module-kind: feature`, LOC <= 100,
   and a module-level `FEATURE` attribute.
2. `scripts/check_no_implicit_state_attribute.py` asserts
   `AppState.__slots__` equals the gate's `APPROVED_SLOTS` frozenset,
   now drained to **empty** (#2299): `AppState` is a thin
   `__slots__ = ()` facade, the cross-cutting mutable primitives a
   frozen slice cannot own live on cohesive owner objects composed onto
   it (`bridge_config` / `per_op_limits` / `request_locks` /
   `ws_auth_limits`), and `clock` / `config` / `startup_time` live in
   `__dict__`. New state must move into a feature state slice or a
   primitive owner.
3. `scripts/check_feature_index_freshness.py` regenerates
   `data/feature_index.json` + `data/codebase_map.json` to a scratch
   path and asserts the committed artefacts match byte-for-byte
   (ignoring the volatile `generated_at` field of the index).

### Ghost-wiring parity (extended in #2149)

`scripts/check_no_ghost_wiring.py` gained a parity pass: every
ENFORCED symbol in `scripts/_ghost_wiring_manifest.txt` must be
claimed by exactly one feature's `ghost_wired_symbols` tuple, and
every claimed symbol must appear in the manifest. The manifest file
stays hand-curated for operational metadata (issue + note); manifests
own the symbol-level inventory.

## Consequences

### Positive

- Adding a new feature is one directory: `state.py`, `feature.py`,
  controllers, MCP descriptor, settings definitions. Zero edits to
  `api/state.py` or `api/app.py`. Locality > centrality.
- AI agents read ONE `feature.py` to learn a feature's whole surface,
  reducing the search space from "grep the whole tree" to "read the
  manifest then drill in".
- Three mechanical gates lock the substrate in. A new feature without a
  `feature.py` fails at pre-push; a new `AppState` slot fails at
  pre-push; a stale index fails at pre-push.

### Negative

- 32 new files (one per feature). Discoverability win, larger PR
  footprint.
- The ghost-wiring symbol/feature mapping is operator-curated; renaming
  a symbol requires updating both the central manifest and the owning
  feature's `ghost_wired_symbols`. The gate catches the divergence but
  cannot auto-fix.
- The freshness gate runs the index generator at pre-push (one extra
  ~10 s during pre-push). Acceptable for the locality win.

### Neutral

- The cross-cutting mutable primitives a frozen slice cannot own
  (request locks, bridge configs, WS timeouts) no longer sit as bare
  `AppState` slots: #2299 decomposed them into cohesive owner objects
  (`bridge_config` / `per_op_limits` / `request_locks` /
  `ws_auth_limits`), leaving `AppState` a thin `__slots__ = ()` facade
  whose only `__dict__` identity is `clock` / `config` /
  `startup_time` plus the background-task sets and shutdown event.
- Boot still warms `synthorg.api.app` before walking `feature.py`
  modules (the latent `core.agent` import cycle resolves through that
  order). The generator + freshness gate include the warmup; the
  ghost-wiring gate avoids it by walking AST instead of importing.

## Alternatives considered

### One central registry instead of per-feature manifests

A `src/synthorg/_core/feature_registry.py` listing every feature in
one file. Rejected: the registry becomes the new central god-object;
adding a feature still requires editing the centre.

### YAML / TOML manifests instead of Python

A `feature.yaml` next to each feature. Rejected: loses Pydantic type
safety (e.g. `state_slice: type[BaseFeatureStateSlice]`), loses
structural typing for `FeatureModule`, requires a parser that
duplicates what `import feature` already does. The 100-LOC cap keeps
the Python file readable.

### Auto-discovered manifests via class-decorators

`@feature(...)` on the state-slice class itself. Rejected: hides the
feature surface inside the slice file; loses the "one file per
feature" property.

### Hand-curated allowlist of feature directories in the gate

Instead of AST-discovering slice-bearing directories. Rejected:
adding a feature requires editing the gate; the substrate's own
definition (a directory with a typed state slice) is the contract;
the gate consults that contract.

## Related

- EPIC #2046: the umbrella program.
- #2048: substrate landing (this ADR's first half).
- #2149: per-feature manifests + enforcement gates (this ADR's
  second half).
- ADR-0006: the tiered module-size policy that gates `feature.py` at
  100 LOC.
- ADR-0010: the AI-navigation index this substrate feeds.
