# ADR-0008: Controller decomposition + composition-root finalisation

## Status

Accepted. Implemented in EPIC #2046 PR 3 (issue #2049), building on the
substrate from ADR-0007.

## Context

ADR-0007 landed the feature-manifest substrate (`discover_features()`,
`FeatureManifest`, topological ordering, slice composition) and the
per-feature `feature.py` manifests. But the substrate was only
half-consumed. `api/app.py` still hardcoded controllers in a 62-entry
`BASE_CONTROLLERS` tuple, the MCP registry and dispatch table merged 20
hand-listed tuples and dicts, and several controllers had grown into
god-modules:

| Module | LOC |
|---|---|
| `api/controllers/providers.py` | 1594 |
| `api/controllers/memory.py` | 1369 |
| `api/controllers/settings.py` | 1086 |
| `auth/controller.py` | 940 |
| `api/controllers/setup_controller.py` | 938 |
| `api/controllers/webhooks.py` | 936 |
| `api/controllers/approvals.py` | 893 |
| `meta/mcp/handlers/infrastructure.py` | 1550 |
| `meta/mcp/handlers/communication.py` | 976 |

These were baselined under ADR-0006's tiered module-size policy
(Section B of the EPIC exemption ledger), pending this PR.

## Decision

### Per-sub-domain controller packages (no re-export bag)

Each god-controller becomes a package of per-sub-domain controller
modules, every module carrying `# module-kind: controller` (400-LOC
cap). Cross-sub-domain helpers and DTOs live in a `_shared` module, not
duplicated. The package `__init__` stays a docstring-only marker: there
is no `__all__` re-export bag, because a re-export bag hides the surface
the substrate is meant to expose. Each sub-controller is claimed by its
co-located `feature.py` manifest's `controllers` tuple. Multiple
controllers may share a Litestar `path`; their routes merge.

The two oversized MCP handler modules decompose the same way into
`meta/mcp/handlers/{infrastructure,communication}/` packages, one module
per sub-domain, each exporting its `<DOMAIN>_HANDLERS` map. The package
`__init__` aggregates the sub-maps into the domain's canonical handler
map. The domain tool-definition modules stay under cap and are not
split.

### Substrate extensions consumed by the composition root

This PR extends ADR-0007's accepted frozen descriptors:

- `ControllerRegistration`: a controller plus an optional readiness
  `predicate` (evaluated against the live `AppState` at route assembly)
  and a `mount` point (`"api"` or `"root"`). A bare controller class is
  equivalent to an unconditional api-mounted registration. The predicate
  preserves the historic 404-when-unwired behaviour for integration and
  optional controllers, so a disabled subsystem 404s rather than
  503-ing every dashboard poll.
- `websocket_handlers`: the real-time `@websocket` handler is a function,
  not a `Controller`; `api_core`'s manifest declares it here.
- `McpHandlerDescriptor.tool_defs` + `handlers_factory`: the feature's
  `MCPToolDef` tuple plus a deferred handler-map loader. Both MCP
  aggregations (`build_full_registry`, `build_handler_map`) iterate
  `discover_features()` instead of the hand-listed tuples. The loader is
  deferred (a callable, not an eager map) so importing a `feature.py`
  during discovery never pulls the service-heavy handler graph.
- `ServiceLifecycleHook`: extends `LifecycleHook` with `stop`. Used for
  feature services only.
- `construction_wirer`: a per-feature `wire_construction(app_state, deps)`
  callable discovered and run in `depends_on` order during the
  construction phase (before persistence connects). The fifteen
  `swap_slice(model_construct(...))` blocks that the old `app.py` ran
  inline became feature-owned `_construction.py` wiring functions, threaded a
  frozen `ConstructionDeps` bundle. A `dataclass` (not Pydantic) so nesting the
  `Phase1Result` / `MeetingWireResult` NamedTuples does not trip Pydantic
  forward-ref introspection.

A small `meta/mcp/feature_descriptors.mcp_descriptor` helper pairs the
eager tool-def tuple with the deferred loader so each manifest declares
its MCP surface in one call.

### Core scaffold stays hand-written

The persistence, bus, task-engine, meeting, backup, settings-dispatcher,
and distributed-backend startup and shutdown sequence is heterogeneous:
per-service timeout budgets are an orchestrator contract, shutdown is
not pure reverse order, and task-engine couples its inner stop timeout
with the outer cancel. This core scaffold stays a hand-written ordered
table run by one generic runner that tracks started services and cleans
up in reverse; it is NOT migrated to the `ServiceLifecycleHook`
protocol. Only feature services use the hook dispatcher. Unifying
the core infrastructure would lose the budgets and coupling and is the
central risk this decision deliberately avoids.

### Conditional and latent-dead controllers

Optional-controller predicates evaluate at construction, where the route
list freezes. Objective and Brownfield controllers depend on a
work-entry adapter that is wired in a startup hook, after route assembly,
so on the standard boot path they never register. The migration
preserves this latent-dead status and locks it with a test rather than
silently registering them.

### Section B exemption lifts

Bringing the centre under its caps lifts ADR-0006's Section B exemptions:

- **God-module allowlist drained.** `api/app.py` (a 147-LOC
  discovery-based composition root), `api/state.py`, `api/auto_wire.py`,
  `api/lifecycle.py`, and `api/lifecycle_builder.py` all fall under their
  code-tier cap, so they drop from `check_no_growth_in_god_modules.py`'s
  net-shrink allowlist and are governed by `check_module_size_budget.py`.
  `core/enums.py` stays until #2051.
- **Mypy narrowed via specific override precedence.** A more-specific
  override beats the `synthorg.api.*` wildcard, so the composition root,
  the lifecycle runners, and the Any-clean decomposed controller packages
  (`providers`, `memory`) hit the full strict++ bar. Modules that
  inherited the work-pipeline `WorkEntryAdapter[Any]` boundary (same as
  `api/state.py`) or other explicit-Any keep the `disallow_any_explicit`
  exemption (the project-wide Any drain is #2056) but re-enable the
  in-scope `unused-awaitable` check.
- **Ruff DOC enforced.** The five ex-god-modules drop their
  `DOC201/202/501` per-file-ignore and carry full docstrings. The
  `BLE001` blind-except drain stays #2062; residual function-complexity
  is governed by the module-size tier budget.

## Consequences

- Locality: adding or changing a feature touches one directory. The
  composition root reads the manifests; there is no central controller
  list or MCP tuple to edit.
- AI-navigable: `data/feature_index.json` records every feature's
  controllers and MCP tools from the manifests.
- Negative: the PR footprint is large, and the substrate descriptors now
  carry richer (controller, tool-def, loader) contributions.

## Alternatives considered

- An `__all__` re-export bag per package: rejected, hides the surface
  the substrate exposes and re-creates a central list at the package
  boundary.
- A central controller registry object: rejected, a new god-object.
- Uniform discovered lifecycle hooks for core infrastructure: rejected,
  loses the hand-written shutdown budgets and task-engine coupling.

## Related

- ADR-0006 (tiered module-size policy): Section B of its exemption
  ledger is lifted by this PR.
- ADR-0007 (feature-manifest substrate): this PR consumes and extends it.
- EPIC #2046; issue #2049.
