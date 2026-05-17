# ADR-0002: Dispatch registry consolidation

## Status

Accepted, implemented in WP-4 (issue #1919).

## Context

Four distinct enum-keyed dispatch surfaces exist in the codebase, in
three different shapes:

1. `communication/event_stream/interrupt_resolution_validators.py`
   `INTERRUPT_RESOLUTION_VALIDATORS`: a `MappingProxyType[InterruptType,
   ResolutionValidator]` callable dict, consumed by
   `InterruptStore.resolve()` as `VALIDATORS[interrupt.type](resolution)`.
2. `settings/type_validators.py` `TYPE_VALIDATORS`: a
   `MappingProxyType[SettingType, Callable]` callable dict, entry point
   `validate_by_type(definition, value)`.
3. `engine/workflow/execution_service.py::_dispatch_node`: a hand-rolled
   six-branch `if/elif` cascade over `WorkflowNodeType` (START, END,
   PARALLEL_SPLIT, PARALLEL_JOIN, AGENT_ASSIGNMENT, VERIFICATION,
   CONDITIONAL, SUBWORKFLOW, TASK), each branch calling a different node
   handler.
4. Two hand-rolled `InterruptType` field-validation cascades:
   `interrupt.py::Interrupt._validate_type_fields` (Pydantic model
   validator) and `api/controllers/events.py::_validate_resume_payload`.
   `settings/models.py::_validate_default_type` is a fifth hand-rolled
   dict-with-fallthrough over `SettingType`.

Costs: the same dispatch concept is expressed three ways (frozen
callable dict, if-cascade, dict-with-fallthrough). Adding a
`WorkflowNodeType` member silently no-ops if a branch is forgotten;
nothing emits a structured observability event on lookup or failure;
the `SettingType` validation map and the `SettingType` default-check
map are independent and can drift.

`synthorg.core.registry.StrategyRegistry[T]` already exists, is
`MappingProxyType`-backed, immutable, and emits `registry.*` events
(built / lookup / failure). It is string-keyed today; every
discriminator above is a `StrEnum`.

## Decision

Extend `StrategyRegistry` to accept `StrEnum` keys transparently
rather than introducing a second registry class. Migrate all four
dispatch surfaces onto it. One ecosystem, one set of `registry.*`
events, exhaustiveness and unknown-key handling centralised.

### Registry change

A single private `_key(k: str | enum.Enum) -> str` normaliser is added.
`__init__` accepts `Mapping[str | StrEnum, Callable]` and freezes keys
through `_key`. `get`, `build`, `__contains__` accept `str | StrEnum`
and route through `_key`. String callers are unchanged: `_key("x")`
returns `"x"`. Enum callers pass `InterruptType.TOOL_APPROVAL` and the
registry stores/looks up `"tool_approval"`. `names()` returns the
stored string keys (the `StrEnum` `.value`s) sorted, unchanged contract.

### Phased plan

**Phase 1 (this PR, low risk: the two surfaces are already dict-shaped)**

- `INTERRUPT_RESOLUTION_VALIDATORS` becomes
  `StrategyRegistry[ResolutionValidator]` keyed by `InterruptType`.
  `InterruptStore.resolve()` calls `_REGISTRY.get(interrupt.type)
  (resolution)`. Unknown type now raises `StrategyFactoryNotFoundError`
  (was a `KeyError`) with a structured `registry.factory.not_found`
  event; `resolve()` maps it to the existing rejection-note path.
- `TYPE_VALIDATORS` becomes a `StrategyRegistry` keyed by `SettingType`.
  `validate_by_type` is unchanged externally.
- `settings/models.py::_validate_default_type` consumes the same
  `SettingType` registry instance (shared module-level singleton), so
  the validation map and the default-check map cannot drift.

**Phase 2 (this PR)**

- `engine/workflow/execution_service.py::_dispatch_node`: each
  `WorkflowNodeType` branch becomes a registered async node handler with
  a uniform `(self, frame, node, ...) -> NodeOutcome` signature.
  START / END / PARALLEL_SPLIT / PARALLEL_JOIN share one
  `_noop_complete` handler. The cascade collapses to
  `handler = _NODE_HANDLERS.get(node.type); return await handler(...)`.
  An unknown member raises `StrategyFactoryNotFoundError` at dispatch
  with a structured event instead of silently falling through.
- `INTERRUPT_FIELD_RULES`: a frozen `Mapping[InterruptType,
  _InterruptFieldRule(interrupt_field, resume_field)]` table backs both
  `Interrupt._validate_type_fields` and `_validate_resume_payload`, so
  the per-type required-field knowledge is declared exactly once and
  both hand-rolled if-cascades are removed. This site deliberately
  uses a frozen data table rather than `StrategyRegistry`: the other
  three sites dispatch *behaviour* (validators / node handlers / type
  coercers) keyed by an enum, whereas here the per-type knowledge is
  *data* (which field is required on which object). `StrategyRegistry`
  is the factory-dispatch seam; applying it to a static 2-entry data
  table would add factory-returning-constant indirection with no
  payoff, contrary to the codebase principle "do not add machinery
  where a typed constant suffices" (see `configuration-precedence.md`,
  "Protocol constants are not settings").

No new AST lint is added. A per-site lint over enum if-cascades would
fire on legitimate `match` statements elsewhere; instead this ADR is
the canonical record of the four sites and review enforces against
re-introduction. `docs/reference/pluggable-subsystems.md` Registries
section is updated to state `StrEnum` keys are accepted.

## Migration mechanics

1. Add `_key` + signature widening to `core/registry/strategy.py`;
   unit-test enum construction, enum lookup, string lookup, mixed,
   unknown-key error, `names()` ordering. Regression-test existing
   string callers (trust, pruning, conflict-detector factories).
2. Per surface: build the registry at module load from the existing
   handler functions; replace the dispatch expression; delete the
   `MappingProxyType` literal / if-cascade.
3. `_dispatch_node`: extract each branch body into a module-level or
   method handler with the uniform signature; assert golden-path
   behaviour per `WorkflowNodeType` before and after (existing
   execution-service tests plus added per-node-type cases).
4. `uv run mypy --num-workers=4 src/ tests/` and the full unit suite
   gate the change.

## Compat scope

None. The `MappingProxyType` literals and if-cascades are deleted in
the same commit that introduces their registry. No alias kept for
`INTERRUPT_RESOLUTION_VALIDATORS` / `TYPE_VALIDATORS`; the few internal
importers move to the registry instance.

## Alternatives considered

- **Second, separate `DispatchRegistry[EnumT, HandlerT]` class.**
  Rejected (user decision): two registry classes to document and
  maintain, two observability-event namespaces, for a discriminator
  difference (`StrEnum` vs `str`) that a one-line normaliser erases.
- **Minimal: only consolidate `_dispatch_node`.** Rejected: leaves the
  three already-dict-ish surfaces in three different shapes, which is
  the inconsistency this ADR exists to remove.
- **Keep status quo.** Rejected: silent no-op risk on new
  `WorkflowNodeType` members; no structured dispatch observability;
  `SettingType` map drift between validation and default-check.

## Consequences

- `core/registry/strategy.py` gains `StrEnum` support used by this ADR
  and available to every future factory; existing string factories are
  untouched and regression-tested.
- Unknown `InterruptType` / `WorkflowNodeType` now raises a typed
  registry error with a structured event instead of `KeyError` or
  silent fall-through; callers that previously caught `KeyError` are
  updated in the same commit.
- RFC-0005 (memory consolidation axis split) consumes the
  `StrEnum`-keyed registry for its strategy factory.
- Out of scope: non-enum dispatch, `match` statements over non-registry
  unions, web / CLI.
