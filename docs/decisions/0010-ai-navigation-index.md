# ADR-0010: AI-navigation index + query tool

## Status

Accepted, implemented in issue #2149.

## Context

ADR-0007 establishes that each feature describes its whole surface in
one `feature.py`. The substrate gives AI agents a stable seam to learn
about a single feature, but to plan a multi-feature change, an agent
must enumerate every manifest. Walking the filesystem 32 times per
plan turn wastes both wall-clock and context window.

What the agent needs is a single document carrying every feature's
surface plus a per-module catalogue of how `src/synthorg/` decomposes:
which file owns which feature, what tier each module sits in, and how
many lines of code it carries. The same data should be queryable
through MCP so an agent can ask "tell me about charter" without
opening any file.

## Decision

### Value objects

`src/synthorg/core/feature_map.py` exposes two frozen Pydantic value
objects with `extra="forbid"`:

- `FeatureMap`: one feature's navigable surface. Fields: `name`,
  `directory`, `settings_namespace`, `protocol_exports`, `controllers`
  (class names), `mcp_tool_names`, `ghost_wired_symbols`,
  `state_slice_fields`, `depends_on`.
- `FeatureIndex`: an aggregate. Fields: `schema_version` (current: 1),
  `generated_at` (timezone-aware datetime), `features` (tuple of
  `FeatureMap`, sorted deterministically by name; duplicate names
  rejected at validation time).

The models live in `core/feature_map.py` rather than being bolted onto
`codebase_structure_map.py`: that module models EXTERNAL brownfield-
imported codebases (the structure-map scanner consumes it); FeatureMap
models the INTERNAL SynthOrg surface. Different concerns, sibling
files.

### Generator

`scripts/generate_feature_index.py` writes two artefacts under
`data/`:

- `feature_index.json`: serialised `FeatureIndex`. One feature per
  entry, sorted by name. Carries the schema version + a UTC build
  timestamp so consumers can detect drift.
- `codebase_map.json`: per-module catalogue. Walks every
  `src/synthorg/**/*.py`, resolves the `# module-kind:` tier via the
  shared `_module_size_lib.resolve_tier` helper, counts LOC via
  `count_loc`, and assigns an `owning_feature` by longest-directory-
  prefix match against the manifests' declared directories.

Both writes are atomic (tempfile + rename). The generator warms the
import graph by importing `synthorg.api.app` first so the per-feature
walk imports against a resolved boot order (otherwise the latent
`core.agent` import cycle trips).

### Freshness gate

`scripts/check_feature_index_freshness.py` regenerates to a scratch
path on every pre-push and asserts the committed artefacts match
byte-for-byte (ignoring the `generated_at` timestamp, which advances
on every run). Missing files are fail-closed: the commit must include
both artefacts. Stale committed artefacts fail with a one-line
"regenerate via `uv run python scripts/generate_feature_index.py`"
prompt.

### MCP query tool

`synthorg_meta_query_feature_map` is registered under the existing
`meta` MCP domain (the natural home for self-describing tools).
Single optional argument `name`: with `name` set, the response carries
one matching `FeatureMap`; without, it carries the full index. The
handler builds the `FeatureIndex` in-memory from `discover_features()`
per call (no file dependency at query time). Unknown `name` returns
an empty `features` list (consistent with the project's read-tool
convention: no 4xx on a clean filter miss). The blank-name guard
lives on the `MetaQueryFeatureMapArgs` model (`NotBlankStr | None`),
so the MCP invoker rejects an empty string before the handler runs.

Tool-count assertions bump from 231 to 232 in both
`tests/integration/mcp/test_tool_surface.py` and
`tests/unit/meta/mcp/test_all_handlers_wired.py`.

## Consequences

### Positive

- An AI agent reads ONE MCP response (or one JSON file) to learn the
  whole feature surface. The plan-turn cost drops from O(N feature
  files) to O(1).
- The generator + freshness gate guarantee the JSON artefacts cannot
  drift from the manifests. A renamed manifest field, a missing
  feature, or a changed controller fails the gate.
- The per-module `codebase_map.json` gives architectural feedback
  loops (tier distribution, LOC per feature) to higher-level audits.

### Negative

- Two generated artefacts in `data/` (~17 k lines of JSON together).
  Each `feature.py` change regenerates both. Acceptable: the
  alternative (re-walking the tree per query) is worse.
- The generator imports the whole `synthorg.api.app` graph at pre-push,
  adding ~3-5 seconds to the freshness gate. Aligned with the cost of
  similar gates (dead-api-endpoints, setting-to-startup-trace).

### Neutral

- The MCP tool name follows the `synthorg_{domain}_{action}`
  convention, hence `synthorg_meta_query_feature_map` rather than the
  shorter `synthorg_query_feature_map`. Per
  `MCPToolDef._NAME_RE = r"^synthorg_[a-z][a-z0-9_]*_[a-z][a-z0-9_]*$"`,
  the registry rejects non-conforming names.

## Alternatives considered

### Single JSON file aggregating both feature_index + codebase_map

Rejected. Different consumers: agents query `feature_index.json` for
feature surfaces; module-size audits query `codebase_map.json` for
per-module tier data. Different cardinalities (32 vs ~1000), different
update cadences, different read patterns. Two files keep the per-
consumer payload small.

### Generator emits JSON to stdout; nothing committed to data/

Rejected. Tests + audits need a stable read surface. Committing the
artefacts + a freshness gate is the canonical pattern (matches
`runtime_stats.yaml`, `schema_drift_baseline.txt`).

### Discover feature surfaces lazily at MCP query time without caching

Rejected. `discover_features()` walks the filesystem and imports every
`feature.py`. Acceptable on a once-per-process basis; not acceptable
on every query.

## Related

- ADR-0006: tiered module-size policy + the `# module-kind:` tier the
  codebase_map records.
- ADR-0007: feature-manifest substrate this index reads.
- #2046: the umbrella EPIC.
- #2149: the implementation issue.
