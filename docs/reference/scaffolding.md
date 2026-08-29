# CLI Scaffolder (`synthorg new`)

`synthorg new` writes a small set of conventions-clean Python files
for a new feature so the opening commit starts from a layout that
already passes ruff, mypy, and every active `scripts/check_*.py`
gate. Wiring into application boot remains a one-time manual step,
documented in the `WIRING.md` each scaffold drops alongside its code.

The scaffolder is a code generator, not a runtime tool. It emits
files; it never modifies existing source. If a target file already
exists the run aborts (override with `--overwrite`).

## Usage

```bash
synthorg new <kind> <domain>
synthorg new <kind> <domain> --dry-run     # preview the file list, write nothing
synthorg new <kind> <domain> --overwrite   # replace existing files
```

`<kind>` is one of `service`, `persistence`, `tool`, `controller`.
`<domain>` is a snake_case identifier (`ping`, `agent_health`); the
scaffolder rejects PascalCase, leading / trailing underscores, and
names that already exist as top-level packages or as Python
keywords.

## Scaffold inventory

### `synthorg new service <domain>`

Lifecycle-managed async service skeleton: `_lifecycle_lock` per
[lifecycle-sync.md](lifecycle-sync.md), `Clock` seam per CLAUDE.md
conventions, structured logging via `synthorg.observability.get_logger`.

Files:

- `src/synthorg/<domain>/__init__.py` -- package marker
- `src/synthorg/<domain>/service.py` -- `<ClassName>Service` with `start` / `stop` and `is_running`
- `src/synthorg/<domain>/errors.py` -- `<ClassName>Error(DomainError)` + `<ClassName>NotFoundError(NotFoundError)` + `<ClassName>AlreadyRunningError(<ClassName>Error)`
- `src/synthorg/observability/events/<domain>.py` -- service lifecycle event constants
- `tests/unit/<domain>/__init__.py` -- test package marker
- `tests/unit/<domain>/test_service.py` -- FakeClock + `asyncio.TaskGroup` smoke
- `src/synthorg/<domain>/WIRING.md` -- registration steps

### `synthorg new persistence <domain>`

Dual-backend repository skeleton (Protocol + SQLite + Postgres +
parametrised conformance test).  The Pydantic entity carries a
single `payload: str` placeholder; `WIRING.md` walks the user
through replacing it with the real entity shape and authoring a
matching revision file under `persistence/<backend>/revisions/`.

Files:

- `src/synthorg/<domain>/models.py` -- `<ClassName>Item` (frozen Pydantic)
- `src/synthorg/persistence/<domain>_protocol.py` -- `<ClassName>Repository`
- `src/synthorg/persistence/sqlite/<domain>_repo.py` -- aiosqlite impl
- `src/synthorg/persistence/postgres/<domain>_repo.py` -- psycopg + pool impl
- `src/synthorg/observability/events/<domain>_repo.py` -- repo event constants (`<DOMAIN>_REPO_*`); the `_repo` suffix keeps this file disjoint from the service scaffold's `events/<domain>.py` so a domain that needs both layers can be scaffolded in either order.
- `tests/conformance/persistence/test_<domain>_repository.py` -- dual-backend assertions
- `src/synthorg/persistence/<domain>_WIRING.md`

### `synthorg new tool <domain>`

MCP tool handler skeleton: typed args model extending
`PaginationFields`, `parse_typed("mcp.tool", ...)` boundary call,
the three `common_logging` helpers, success log via
`MCP_HANDLER_INVOKE_SUCCESS`.

Files:

- `src/synthorg/meta/mcp/handlers/<domain>.py` -- `_list` handler + `<ClassName>ListArgs`
- `tests/unit/meta/mcp/handlers/test_<domain>.py` -- args validation + envelope coverage
- `src/synthorg/meta/mcp/handlers/<domain>_WIRING.md` -- registration in `meta/mcp/domains/`

### `synthorg new controller <domain>`

Litestar controller + service-layer facade. The controller routes
through a `_service(state)` factory (persistence-boundary rule).
Endpoints: list / get / delete with `require_read_access` /
`require_write_access` guards.

Files:

- `src/synthorg/api/controllers/<domain>.py` -- `<ClassName>Controller`
- `src/synthorg/api/services/<domain>_service.py` -- `<ClassName>Service` facade
- `tests/unit/api/controllers/test_<domain>.py`
- `src/synthorg/api/controllers/<domain>_WIRING.md`

## Why a scaffolder

Code review and audit runs kept finding the same categories of
violations in new code: services without a `_lifecycle_lock`,
repositories logging mutations directly, MCP handlers parsing dicts
without `parse_typed`, controllers calling `app_state.persistence.*`
without a service layer. The scaffolder shapes new code correctly
from line 1; the gates listed in the **Convention Rollout** section
of `CLAUDE.md` catch any drift afterwards.

The shape contract is locked by the Go-side tests under
`cli/internal/scaffold/*_test.go`: each rendered file is asserted
to contain the conventional tokens (event constant imports,
`spec=` on every Mock, `parse_typed` boundary calls, etc.). When a
project convention changes, fix the template AND update the test in
the same PR.

## Feature package structure

Every business-domain package under `src/synthorg/` ships a `feature.py`
manifest. Packages that own a state slice additionally ship `state.py` and
`_construction.py`, together forming a three-file structural triple alongside
the service / repository / controller modules; a slice-less package (for
example `deliverable_receipts` or `providers/management`) ships only
`feature.py`. The triple is an architectural requirement, not scaffolder
output: `synthorg new <kind>` generates the four scaffold kinds (`service` /
`persistence` / `tool` / `controller`) described earlier, while these files are
hand-maintained per package.

- **`feature.py`** declares a module-level `FEATURE: FeatureModule =
  FeatureManifest(...)` constant describing the package's full surface:
  controllers, MCP handlers, lifecycle hooks, the state-slice class, the
  `construction_wirer`, `ghost_wired_symbols`, and `depends_on`.
  `ghost_wired_symbols` lists boot-constructed class names that are wired
  during `_construction.py` but not directly referenced by the manifest's
  `state_slice` (so the unwired-symbol gates do not flag them).
- **`state.py`** (slice-bearing packages only) defines the
  `<Name>StateSlice(BaseFeatureStateSlice)` frozen Pydantic model and its
  `<name>_of(app_state)` accessor (see conventions §32).
- **`_construction.py`** (slice-bearing packages only) exports
  `def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:`
  with that exact signature; the composition root calls it during the
  construction phase.

Representative example: `src/synthorg/budget/{feature,state,_construction}.py`.
A new slice-bearing package must ship all three; a slice-less one ships only
`feature.py`.
