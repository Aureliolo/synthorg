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
  indirect) up to `api`, `persistence`, `engine`, `workers`, or `meta`.
  `core` is the bottom layer; everything may depend on it, it depends on
  nothing above it.
- **persistence-app-boundary** -- `synthorg.persistence` does not
  *directly* import `api` or `workers`. Domain-model imports into
  `engine` and `meta` are legitimate (repositories serialise those
  models) and are not forbidden.
- **observability-below-api** -- `synthorg.observability` does not
  *directly* import `api`.

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

## Changing the contracts

- **Adding a contract**: add a `[importlinter:contract:<id>]` block, run
  `lint-imports`, and confirm it reports `KEPT`. A contract that does not
  pass today is a code change, not a config change.
- **Blessing a new back-edge**: add the exact `module -> module` line to
  that contract's `ignore_imports`. Prefer fixing the import; bless only
  edges that are genuinely intended (a wiring seam or shared singleton).
