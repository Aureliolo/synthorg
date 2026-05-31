# ADR-0009: Declarative import-layering contracts

## Status

Accepted. Implemented in EPIC #2046 PR 4 (issue #2050), building on the
module-size policy (ADR-0006), the feature-manifest substrate (ADR-0007),
and the composition-root finalisation (ADR-0008).

## Context

Three custom AST gates already enforce specific import invariants:

- `scripts/check_persistence_boundary.py` -- only `persistence/` may import
  `sqlite`/`psycopg` or emit raw SQL.
- `scripts/check_no_api_dto_in_persistence_or_service.py` -- API DTO types
  must not leak into the persistence or service layers.
- `scripts/check_dependency_inversion.py` -- callers depend on protocols,
  not concrete classes, across the marked boundaries.

Each gate answers one narrow question. None expresses the *layering* of the
package graph as a whole: which subsystems are foundational and may not
import upward, which app-boundary edges are forbidden. That rule lived only
in `CLAUDE.md` prose and reviewer judgement, so a new upward import (for
example `core` reaching into `api`, or `persistence` importing a worker)
would pass every mechanical gate.

A naive fix -- a strict total-order `layers` contract -- does not fit the
real graph. The codebase routes deliberate cross-subsystem references
through shared hubs: the per-domain `.state` slices import
`api.state_slices`; `config.schema` transitively reaches most subsystems;
the construction-wiring seam and `api.auth.system_user` are imported by
persistence on purpose. A total order cannot express those back-edges
without a sprawling `ignore_imports` list that would itself drift.

## Decision

Add `import-linter` (pinned `import-linter==2.11`) with a committed,
**reality-grounded** `.importlinter` at the repo root. Every contract passes
against the *current* import graph; the file exists to prevent regressions,
not to describe an aspirational architecture.

### Contracts (all `type = forbidden`, direct imports only)

- **core-is-foundation** -- `synthorg.core` must not import `api`,
  `persistence`, `engine`, `workers`, or `meta`.
- **persistence-app-boundary** -- `synthorg.persistence` must not directly
  import `api` or `workers`.
- **observability-below-api** -- `synthorg.observability` must not directly
  import `api`.

All three set `allow_indirect_imports = true` (they check DIRECT edges; the
transitive graph legitimately converges again through `config.schema` and the
`.state` hubs) and bless the handful of intentional direct back-edges in
`ignore_imports`: the `persistence._construction` wiring seam, the per-domain
`.state` slices (`-> api.state_slices`), `api.auth.system_user` (imported by
the user repositories), and the three `observability -> api` feature/startup
edges. A strict total-order `layers` contract is deliberately NOT used.

### The three custom AST gates are retained, not replaced

`import-linter` reasons about module-to-module edges. It cannot see raw SQL
strings, DTO type leakage, or concrete-vs-protocol dependency direction.
Those remain the job of the three custom gates. `.importlinter` and the
custom gates are complementary; `docs/reference/import-layering.md` records
the split.

### Enforcement

`uv run lint-imports --config .importlinter` runs as a pre-push hook
(`pass_filenames: false`, `stages: [pre-push]`, listed in `ci: skip:`) and as
a step in the `lint` job of CI.

## Consequences

- A new upward or cross-boundary direct import fails mechanically at push,
  closing the gap that previously relied on review.
- The deliberate back-edges are documented as code in `ignore_imports`; a new
  back-edge must be added explicitly and justified, rather than appearing
  silently.
- Because the contracts are regression-preventing (not aspirational), the
  file stays green day-to-day and does not become a source of busywork. When
  a future refactor removes a back-edge, its `ignore_imports` line is deleted
  in the same change (no legacy aliases).
- `import-linter` complements rather than duplicates the custom AST gates;
  removing any one of them would reopen a distinct hole.
