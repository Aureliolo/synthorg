# Import layering

SynthOrg enforces its module-dependency rules with two complementary
mechanisms: declarative contracts checked by
[import-linter](https://import-linter.readthedocs.io/) and three
codebase-specific AST gates. Together they cover the shapes of
layering badness that matter here without pretending the architecture
is a clean total order.

## Declarative contracts (`.importlinter`)

`import-linter` (backed by `grimp`) builds the full static import graph
of `synthorg` and checks the contracts in the repo-root `.importlinter`
file. Run it locally with:

```bash
uv run lint-imports --config .importlinter
```

It runs as a `pre-push` hook (`lint-imports` in `.pre-commit-config.yaml`)
and as a step in the `Lint` CI job.

The contracts are **reality-grounded**: each one passes against the
current import graph and exists to stop regressions, not to describe an
aspirational layering. The current contracts are:

- **core-is-foundation** -- `synthorg.core` has no import path (direct or
  indirect) up to `api`, `persistence`, `engine`, `workers`, `meta`,
  `tools`, `providers`, `communication`, `budget`, or `config`. `core` is
  the bottom layer; everything may depend on it, it depends on nothing
  above it. (`security` is intentionally absent: `core.company` reads a
  small set of `security` config models, a deliberate pre-existing edge.)
- **execution-is-a-leaf** -- `synthorg.execution` (the light leaf holding
  `TurnRecord`, the trajectory enums, `EfficiencyRatios`, the
  `ExecutionResultView` protocol, and `ParkedContext`) has no path up to
  `engine`, `api`, `workers`, or `meta`. `engine` depends downward on the
  leaf; the leaf never reaches back into `engine`. See
  [ADR-0012](../decisions/0012-cold-import-cycle-break.md).
- **persistence-app-boundary** -- `synthorg.persistence` does not
  *directly* import `api` or `workers`. Domain-model imports into
  `engine` and `meta` are legitimate (repositories serialise those
  models) and are not forbidden.
- **observability-below-api** -- `synthorg.observability` does not
  *directly* import `api`.
- **no-business-logic-upward-into-api** -- `tools`, `engine`, `meta`,
  `security`, and `integrations` do not import `api.services`, `api.auth`,
  or `api.dto`. The cross-layer-shared validators and utilities that used
  to live under `api` (`parse_typed`, the auth-token-size helpers, the
  sliding-window limiter, retry-after parsing) moved into `core.*` leaves,
  so business logic names them from `core` and never reaches up into the
  HTTP layer. See [ADR-0013](../decisions/0013-layering-dependency-inversion.md).
- **controllers-no-persistence-internals** -- `synthorg.api.controllers`
  and `synthorg.api.auth.controllers` do not import `persistence._shared`,
  `persistence.sqlite`, `persistence.postgres`,
  `persistence.constraint_tokens`, or `persistence.jsonb_capability`.
  Controllers reach persistence through a service or the repository
  protocols; backend internals, raw-SQL helpers, and constraint-token
  strings stay behind the boundary. See
  [ADR-0013](../decisions/0013-layering-dependency-inversion.md).

### Why direct-only, and the ignore lists

The app-boundary contracts set `allow_indirect_imports = true`, so they
check only **direct** imports. The codebase routes many cross-subsystem
references through shared hubs: every per-domain `*.state` slice imports
`api.state_slices`, and `config.schema` transitively reaches most
subsystems. Those transitive paths are unavoidable and not meaningful as
layering violations, so the contracts target the direct edges that *are*
meaningful.

A small number of deliberate direct back-edges are blessed in each
contract's `ignore_imports`:

- the construction-wiring seam (`persistence._construction ->
  api.construction_wiring` / `api.state`),
- the per-feature state slice (`persistence.state -> api.state_slices`),
- the shared system user (`persistence.{sqlite,postgres}.user_repo ->
  api.auth.system_user`),
- the metrics collector reading app state
  (`observability.{prometheus_collector,startup_wiring} -> api.state`,
  `observability.feature -> api.controllers.metrics`).

### Why no total-order `layers` contract

A strict `layers` contract enumerates a top-to-bottom order and forbids
every upward edge. The real graph keeps deliberate back-edges (the state
slices and `system_user` above; `core <-> observability` for the logger
seam) that a total order cannot express without a sprawling
`ignore_imports` list, so we use targeted `forbidden` contracts instead.

## Custom AST gates (not replaced by import-linter)

Three gates check rules that are about *how* an import is used, which a
graph-level tool cannot see:

- **`scripts/check_persistence_boundary.py`** -- only
  `src/synthorg/persistence/` may import `sqlite3` / `aiosqlite` /
  `psycopg` or emit raw SQL. The real persistence boundary is about
  driver and raw-SQL access, not package-level import direction.
- **`scripts/check_no_api_dto_in_persistence_or_service.py`** -- API DTO
  modules must not be imported inside `persistence/` or service layers.
- **`scripts/check_dependency_inversion.py`** -- callers depend on
  repository *protocols*, not concrete backend classes.

## Cold-import smoke test (`tests/unit/test_cold_import.py`)

The declarative contracts cannot see the worst class of import bug this
codebase has hit: a **cold-import cycle** driven by eager re-export side
effects in a package `__init__`, not by a simple module-to-module edge.
Importing any submodule runs its package `__init__`, and a hub `__init__`
that eagerly re-exports its implementation pulls a large subgraph as a
side effect. `grimp` builds its graph from explicit `import` statements,
so it reports such a contract `KEPT` even when a fresh-interpreter import
of the leaf raises `ImportError` (see
[ADR-0012](../decisions/0012-cold-import-cycle-break.md)).

The regression guard is therefore a runtime smoke test: each leaf in
`COLD_IMPORT_LEAVES` is imported in its own freshly spawned interpreter
via `subprocess`, which is the only faithful way to assert a *cold*
import (within the pytest process the graph is already primed by
`tests/conftest.py`). The `forbidden` contracts above are a secondary,
documentation-grade backstop.

To keep a leaf cold-importable, do not add eager implementation
re-exports to a package `__init__` on its import path; keep hub inits to
light abstractions (config / models / protocols) or empty, and place
genuinely shared types in a light leaf package (`core.*` or
`execution.*`) rather than reaching up into a heavy hub.

## Changing the contracts

- **Adding a contract**: add a `[importlinter:contract:<id>]` block, run
  `lint-imports`, and confirm it reports `KEPT`. A contract that does not
  pass today is a code change, not a config change.
- **Blessing a new back-edge**: add the exact `module -> module` line to
  that contract's `ignore_imports`. Prefer fixing the import; bless only
  edges that are genuinely intended (a wiring seam or shared singleton).
