# ADR-0012: Break the cold-import cycle

## Status

Accepted. Implemented in issue #2207.

## Context

`synthorg` could not be imported starting from an arbitrary leaf module on a
*cold* interpreter. Both `python -c "import synthorg.providers.enums"` and
`python -c "import synthorg.core.agent"` raised a circular-import `ImportError`
from a fresh process. Every real entry point hid the defect by importing a
heavy package first, which happened to resolve the graph in a working order:

- the app boots through `api.app`;
- the test suite's root `tests/conftest.py` imported `synthorg.persistence`
  first;
- `evals/__main__.py` imported `synthorg.persistence` before its runner.

Any new standalone entry point had to rediscover that magic prime, and the
`.importlinter` contracts passed only because the working order was never
violated.

### Root cause: eager re-export side effects in package inits

The cycle was **not** a set of simple module-to-module edges. It was driven by
eager re-export side effects in package `__init__` hubs. Importing any
`persistence.*` submodule runs `persistence/__init__.py`; that init eagerly
imported `factory -> protocol -> (everything)`, so a cold
`import synthorg.persistence._shared` (a leaf datetime helper) dragged in over
a thousand `synthorg` modules, including `config.schema`, `core.agent`,
`engine.agent_engine`, and `providers.management`. The same was true of
`config`, `providers`, `providers.management`, `budget`, and the
`conflict_resolution` / `escalation` sub-packages.

A consequence that shaped the regression guard: `grimp` (and therefore
`import-linter`) builds its graph from explicit `import` statements per module.
It cannot see a cycle that exists only because importing a leaf triggers a
parent `__init__` whose re-exports pull a subgraph. `grimp`'s chain-finder
returned `None` for `communication.config -> config.schema` even though the
runtime cycle ran through it, so a "forbidden indirect" contract reported
`KEPT` while the cycle was live.

`core/__init__.py` is the one deliberately light package init (empty by
design). That is why relocating shared types into a `core.*` (or a new
`execution.*`) leaf genuinely breaks edges, while relocating into any other
package does not.

### The three named keystone cycles

1. `config.schema <-> communication.config`
2. `core.agent <-> persistence.protocol`
3. `budget.coordination_collector <-> engine` (still dormant; the #2183 hoist
   programme is underway and applies this same relocation strategy where a
   module-level forward-ref would otherwise close a cycle -- for example
   `workers/distributed_protocols.py`, a cold-safe handle leaf for the separate
   `workers.config -> communication.config` cycle -- but the
   `budget.coordination_collector` edge itself is not yet hoisted)

Because #2183 will remove the `TYPE_CHECKING` guards, guarding these edges with
`if TYPE_CHECKING:` is not a durable fix. The cycles had to be broken
structurally, by relocation.

## Decision

### Cut A: relocate `ToolSubConstraints` into a `core` leaf

`core.agent` reached up into the heavy `tools` hub for `ToolSubConstraints`.
`ToolSubConstraints`, its five dimension enums, `_LEVEL_SUB_CONSTRAINTS`, and
`get_sub_constraints` moved verbatim from `tools/sub_constraints.py` to the new
`core/tool_constraints.py` (its only dependencies are `core.*` and
`observability`, all light). The old module was deleted and all importers
updated -- a full rename, no re-export shim.

### Cut B: trim the `escalation` / `conflict_resolution` package inits, and the linchpin hub inits

The `config.schema <-> communication.config` cycle closed through a back-path
`communication.config -> conflict_resolution.config -> escalation/__init__ ->
factory -> in_memory_store -> persistence`, which then reached
`persistence.protocol -> providers.management -> service -> config.schema`. The
fix was to stop the package inits on that path from eagerly importing their
implementation:

- `conflict_resolution/escalation/__init__.py` now re-exports only the light
  abstractions (`config`, `models`, `protocol`), not `factory` /
  `in_memory_store` / `notify` / `processors` / `registry` / `sweeper`.
- The linchpin `persistence.protocol -> providers.management -> config.schema`
  edge was severed by emptying the eager re-exports of
  `providers/management/__init__.py`, `config/__init__.py`,
  `persistence/__init__.py`, and `budget/__init__.py`, and repointing
  `providers.management.capability_dtos` at `config.provider_schema` (a leaf)
  instead of `config.schema`.

Callers now import implementation symbols from their defining submodule. After
this, `config.schema` imports cold, which is what proves the named cycle is
broken from the config side.

### Cut C: a new light leaf package `synthorg/execution/`

`ExecutionResult` is welded to `engine` by its `context: AgentContext` field
and cannot move. So instead of moving it, the types
`budget.coordination_collector` actually needs were relocated into a new light
leaf package, and the collector was given a structural view to depend on:

- `execution/turn.py` -- `NodeType`, `BehaviorTag`, `TurnRecord` (moved out of
  `engine/loop_protocol.py`). `TerminationReason` stays in
  `engine/loop_protocol.py` alongside `ExecutionResult`.
- `execution/efficiency.py` -- `EfficiencyRatios`, `IdealTrajectoryBaseline`
  (moved out of `engine/trajectory/efficiency_ratios.py`).
- `execution/view.py` -- a `@runtime_checkable ExecutionResultView` protocol
  exposing only `turns: tuple[TurnRecord, ...]`, the minimal surface the
  collector reads.
- `execution/parked_context.py` -- `ParkedContext`, the serialised
  parked-agent snapshot (moved out of `security/timeout/`). It applies the
  same leaf-placement rule: the persistence, worker, and API layers name the
  type without dragging the heavy `engine` package in, breaking an
  engine<->security edge that the `ParkService` relocation to `engine/`
  surfaced.

`engine.loop_protocol` and `engine.trajectory` now import the moved types from
the leaf (a legitimate downward dependency); `ExecutionResult` structurally
satisfies `ExecutionResultView`. `budget.coordination_collector` imports the
view and `TurnRecord` from `execution.*` at module level and never imports
`engine`, so it stays cold-safe even after #2183 hoists its annotations.

### Leaf-placement rule

Shared types that would otherwise force a cross-hub edge belong in a light leaf
package -- `core.*` for foundation types, `execution.*` for execution-trace
types -- never reached for by importing up into a heavy hub. Hub `__init__`
files stay light (config / models / protocols) or empty; implementation is
imported from its defining submodule.

Applying this rule, the dependency-free leaves pinned in `COLD_IMPORT_LEAVES`
now include, beyond the three original cuts: `core.completion_enums`
(`FinishReason`, out of the `providers` hub so `budget.cost_record` no longer
re-enters it), `core.effective_autonomy` and `core.redteam_review_input`
(resolved-autonomy and red-team gate-input value objects out of the heavy
`security` package), and `core.approval`. New dependency-free `core.*` /
`execution.*` types are added to the same tuple as they appear.

### Regression guard

The primary guard is a runtime cold-import smoke test,
`tests/unit/test_cold_import.py`: each leaf in `COLD_IMPORT_LEAVES` is imported
in its own freshly spawned interpreter via `subprocess`, the only faithful way
to assert a cold import (within pytest the graph is already primed). A
secondary, documentation-grade backstop extends `.importlinter`:
`core-is-foundation` now also forbids `core` reaching up into `tools`,
`providers`, `communication`, `budget`, and `config`; a new
`execution-is-a-leaf` contract forbids `execution` reaching up into `engine`,
`api`, `workers`, or `meta`. The smoke test exists precisely because these
contracts cannot see init-side-effect cycles.

## Consequences

- The two acceptance leaves and `evals` import cold; the `import
  synthorg.persistence` prime in `evals/__main__.py` was removed.
- The forward chain `providers -> budget -> security -> engine ->
  communication -> persistence` stays heavy but is now acyclic. Heaviness is
  not the bug; the cycle was. We did **not** lazify the hubs (rejected: it
  fights mypy-strict, the explicit `__all__` contracts, and `engine`'s
  registry side effects).
- Emptying the hub inits means importing an arbitrary leaf no longer drags the
  rest of the graph in a known-good order. The test process therefore keeps an
  explicit graph prime, repointed from the now-empty `persistence` init to
  `import synthorg.api.app` in `tests/conftest.py` (the real entry point, which
  loads every hub in a proven order). This primes the **test** process only;
  production leaf entry points are genuinely cold-safe, which the
  subprocess-isolated smoke test verifies. Only the `evals` prime was removed;
  the test-conftest prime is preserved by design (the issue scopes its removal
  to `evals`).

## Scope notes

This change is broader than the three cuts in isolation, and intentionally so:

- **Hub inits leaned:** `providers/management`, `config`, `persistence`,
  `budget`, and the `conflict_resolution/escalation` init, in addition to the
  three relocations. Each was load-bearing for the acceptance cold imports.
- **`engine/__init__` deliberately left heavy.** `engine` builds dispatch
  registries at import of the modules it eagerly pulls (`execution_loop`,
  `conflict_detector`, etc.). Making that lazy risks "registry empty" runtime
  bugs that an import smoke test would not catch. The leaf relocation (Cut C)
  removes the need to touch `engine`'s init.

## Out of scope

- **The `communication <-> engine` cycle** (via `communication.meeting._prompts
  -> engine.prompt_safety`) is a separate, unnamed cycle. `communication.config`
  consequently does not yet import cold and is intentionally absent from
  `COLD_IMPORT_LEAVES`. Breaking it would require leaning `engine`'s init (see
  above). `config.schema` importing cold already discharges the named
  `config.schema <-> communication.config` acceptance cycle.
- **Broad hub lazification** (PEP 562 `__getattr__` across the heavy package
  inits) remains a larger refactor out of scope here. Targeted single-module
  lazification is the documented exception: `synthorg.approval.__init__`
  lazily exports `ApprovalStoreProtocol` (mirroring `synthorg.ontology`) so
  importing the `approval.enums` / `approval.models` leaves does not eagerly
  pull `approval.protocol -> core.approval` and close that package-init cycle.
