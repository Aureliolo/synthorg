# Feature manifest reference

A feature manifest is one file (`feature.py`) per feature directory
that declares the feature's whole surface for the codebase: settings
namespace, typed state slice, REST controllers, MCP tool contributions,
lifecycle hooks, boot-constructed (ghost-wired) symbols, and the
features it depends on. An AI agent reads this one file to learn what
a feature ships; new features add one without touching the centre.

The substrate lives at [`src/synthorg/_core/features.py`](../../src/synthorg/_core/features.py)
and is enforced by three convention gates:
[`check_feature_manifest.py`](../../scripts/check_feature_manifest.py),
[`check_no_implicit_state_attribute.py`](../../scripts/check_no_implicit_state_attribute.py),
and [`check_feature_index_freshness.py`](../../scripts/check_feature_index_freshness.py).
The architecture decisions are recorded in
[ADR-0007](../decisions/0007-feature-manifest-substrate.md) and
[ADR-0010](../decisions/0010-ai-navigation-index.md).

## What counts as a feature

A directory under `src/synthorg/` is a feature exactly when one of its
`*.py` files (typically `state.py`, sometimes `<name>_state.py` such as
`api/api_core_state.py`) declares a class that inherits from
`BaseFeatureStateSlice`. The substrate's own definition of "feature"
is therefore "a directory with a typed state slice"; the
`check_feature_manifest` gate walks the tree under this rule.

A directory without a state slice is not a feature and does not need a
`feature.py`. The gate accepts that.

## Manifest fields

```python
# module-kind: feature
"""Charter feature manifest."""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.charter import CharterController
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.mcp.domains.charter import CHARTER_TOOLS
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="charter",
    settings_namespace=SettingNamespace.CHARTER,
    state_slice=CharterStateSlice,
    controllers=(CharterController,),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="charter",
            tool_names=tuple(tool.name for tool in CHARTER_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=("CharterInterviewService", "CharterDispatcher"),
    depends_on=(),
)
```

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `NotBlankStr` | Stable feature name. Must be unique across the tree. |
| `settings_namespace` | `SettingNamespace \| None` | The `SettingNamespace` enum value the feature owns, or `None` when the feature has no operator-facing namespace. |
| `state_slice` | `type[BaseFeatureStateSlice] \| None` | The feature's typed slice class; the empty slice composer constructs one of these at boot. |
| `controllers` | `tuple[type[Controller], ...]` | Litestar controller classes the feature registers. |
| `mcp_handlers` | `tuple[McpHandlerModule, ...]` | Frozen `McpHandlerDescriptor` instances declaring the feature's MCP domain + tool names. |
| `lifecycle_hooks` | `tuple[LifecycleHook, ...]` | Named async startup hooks (current PR ships none directly through manifests; the field is forward-compatible for the Part-3 composition root). |
| `ghost_wired_symbols` | `tuple[str, ...]` | Boot-constructed class / factory names the feature owns. Must match `scripts/_ghost_wiring_manifest.txt` exactly at the symbol level. |
| `depends_on` | `tuple[str, ...]` | Names of other features this feature depends on. The resolver loads dependencies first. |

## Header + LOC ceiling

Every `feature.py` carries `# module-kind: feature` on the first
non-blank line (the tier header the module-size budget gate reads).
The `feature` tier caps LOC at 100; a manifest larger than that is a
sign the feature is doing real work in the manifest, which is the
wrong place. Move logic into the feature's services / factory / state
modules and keep the manifest declarative.

## Three convention gates

### `check_feature_manifest.py`

For every slice-bearing directory, asserts:

- a sibling `feature.py` exists;
- the first non-blank line is `# module-kind: feature`;
- LOC <= 100 (the `feature` tier cap);
- a module-level `FEATURE` attribute is declared.

Deleting any `feature.py` fails the gate. Adding a new feature
without one fails the gate. There is no baseline (the contract is
absolute).

### `check_no_implicit_state_attribute.py`

Parses `src/synthorg/api/state.py` for `AppState.__slots__` and
asserts it equals the gate's hard-coded `APPROVED_SLOTS` frozenset (21
slots today: the cross-cutting mutable primitives a frozen slice
cannot own, plus `clock`, `config`, `startup_time`).

Growing a slot fails the gate; the contributor either:

- moves the new state into a feature state slice
  (`BaseFeatureStateSlice` subclass), or
- updates `APPROVED_SLOTS` with a rationale in the same change (the
  gate edit IS the decision record).

Removing a slot also fails (the gate is stale until the contributor
trims `APPROVED_SLOTS` to match).

### `check_feature_index_freshness.py`

Regenerates `data/feature_index.json` + `data/codebase_map.json` to a
scratch path and asserts the committed files match byte-for-byte
(ignoring the volatile `generated_at` timestamp). Missing files are
fail-closed. To fix a freshness failure:

```bash
uv run python scripts/generate_feature_index.py
git add data/feature_index.json data/codebase_map.json
```

## Ghost-wiring symbol claims

`scripts/check_no_ghost_wiring.py` performs symbol-level parity
between `scripts/_ghost_wiring_manifest.txt` (state + operational
metadata) and the union of every `feature.py`'s `ghost_wired_symbols`.
Every ENFORCED symbol in the manifest must be claimed by exactly one
feature; every claimed symbol must appear in the manifest. The
manifest stays the canonical place for the issue + note text (it is
operational, not architectural).

Claims follow the defining-module rule: a symbol's claim goes to the
feature that owns its definition, not the feature that constructs it
at boot. For example, `build_coordinator` is defined in
`engine/coordination/factory.py` and called from
`workers.runtime_builder`; the claim is on the `engine` manifest.

The parity check is AST-driven, so `ghost_wired_symbols=(...)` MUST
hold string literals, not module-level constants. Writing
`ghost_wired_symbols=(MY_SYM,)` (a `Name` node) silently drops the
claim from the gate's union; the gate would then incorrectly flag
the symbol as orphan. Always use string literals.

## Discovering features at runtime

`synthorg._core.features.discover_features()` walks the filesystem for
`feature.py` modules, imports each, and returns them in dependency
order (independents sorted by name; cycles + duplicates raise
`FeatureDependencyError`). The result is cached at module level;
the index generator and the freshness gate pass `force=True` to
rebuild.

`feature_directories()` maps feature name -> repo-relative directory
(e.g. `charter -> src/synthorg/meta/charter`). The navigation-index
generator consumes this mapping to assemble per-feature surfaces and
to assign `owning_feature` in the codebase map.

## Querying the index via MCP

The `synthorg_meta_query_feature_map` MCP tool returns the same
`FeatureIndex` shape carried in `data/feature_index.json`. Pass an
optional `name` argument to filter to a single feature:

```text
synthorg_meta_query_feature_map(name="charter")
```

The handler builds the index in-memory from `discover_features()` per
call (so the result reflects the running tree, not a possibly stale
artefact on disk).

## Adding a new feature

1. Create the directory under `src/synthorg/<feature_name>/`.
2. Add `state.py` exposing a `<Name>StateSlice(BaseFeatureStateSlice)`.
3. (Optional) Add controllers under `src/synthorg/api/controllers/`.
4. (Optional) Add MCP domain + handler under `meta/mcp/domains/` +
   `meta/mcp/handlers/`.
5. (Optional) Register a new `SettingNamespace` value + add
   `src/synthorg/settings/definitions/<name>.py`.
6. Add `feature.py` carrying `# module-kind: feature` and the
   `FEATURE` manifest with the correct fields.
7. Regenerate the navigation index:
   `uv run python scripts/generate_feature_index.py`.
8. If your feature owns new boot-constructed symbols, add them to the
   manifest's `ghost_wired_symbols` AND to
   `scripts/_ghost_wiring_manifest.txt`.

The three gates plus the ghost-wiring parity check guarantee that any
missing step fails at pre-push.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `missing feature.py (slice-bearing directory needs a feature manifest)` | New state slice without a manifest. | Add `feature.py` per the guide above. |
| `missing '# module-kind: feature' header on first non-blank line` | Module docstring or other content above the header. | Move the header to be the first non-blank, non-shebang line. |
| `LOC <n> exceeds feature-tier cap 100` | Logic crept into the manifest. | Move logic out into the feature's services / factory modules; keep `feature.py` declarative. |
| `missing module-level FEATURE attribute` | Typo in the attribute name, or `FEATURE` set inside `if __name__ == "__main__":`. | Assign `FEATURE: FeatureModule = FeatureManifest(...)` at module top level. |
| `data/feature_index.json is stale` | `feature.py` changed; index was not regenerated. | Run `uv run python scripts/generate_feature_index.py` and commit. |
| `AppState.__slots__ grew with un-approved attributes` | Service was bolted onto `AppState` instead of a slice. | Move the field into a `BaseFeatureStateSlice` subclass on the owning feature. |
| `ghost-wiring parity: ENFORCED symbols missing from every feature.py` | New manifest entry without a feature claim. | Add the symbol to the owning feature's `ghost_wired_symbols`. |
| `ghost-wiring parity: symbols claimed by a feature.py but missing from manifest` | Feature claims a symbol that was removed from the central manifest. | Either restore the manifest entry or remove the feature's claim, depending on intent. |
