# ADR-0005: Memory consolidation strategy axis split

## Status

Accepted, implemented in WP-4 (issue #1919).

## Context

`memory/consolidation/` ships one `ConsolidationStrategy` Protocol with
a single `consolidate(entries, *, agent_id) -> ConsolidationResult`
method and three implementations:

- `SimpleConsolidationStrategy`: group by category, keep the
  highest-relevance entry per group (recency tiebreak), summarise the
  rest by truncated concatenation.
- `DualModeConsolidationStrategy`: group by category, classify each
  group sparse/dense by majority vote, route dense groups to an
  extractive key-fact preserver and sparse groups to an abstractive
  LLM summariser.
- `LLMConsolidationStrategy`: group by category, keep the
  highest-relevance entry, feed the rest to an LLM for synthesis with
  concatenation fallback.

Every strategy entangles two orthogonal decisions in one class: **which
entries to consolidate** (grouping + keep/remove partitioning) and
**how to consolidate the removed ones** (concatenate / extract /
abstract / LLM-synthesise). The "group by category, keep top relevance"
selection is copy-pasted across Simple and LLM; the concatenation
fallback is duplicated. Adding a fourth selection rule or a fifth
operation today means a new monolithic class re-deriving the other
axis. There is no config discriminator and no factory: the service
takes a hand-injected strategy.

SynthOrg is pre-alpha. This is the correct window to split the axes
with no compatibility layer.

## Decision

Decompose into two orthogonal protocols plus a composite, all under
`memory/consolidation/`:

All three current strategies share an identical selection step
(group by `category`, drop groups below `group_threshold`, keep the
entry with the highest `relevance_score` -- `None` treated as `0.0` --
with most-recent `created_at` as the tiebreak). They differ only in
how the to-remove set becomes a stored summary. So the split is **one
selector, a family of ops**, not a selector-per-strategy:

```python
@runtime_checkable
class EntrySelector(Protocol):
    def select(
        self, entries: tuple[MemoryEntry, ...]
    ) -> tuple[SelectionGroup, ...]: ...
        # SelectionGroup: (category, kept: MemoryEntry,
        #                  to_remove: tuple[MemoryEntry, ...])

@runtime_checkable
class ConsolidationOp(Protocol):
    async def consolidate(
        self,
        to_remove: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
        category: MemoryCategory,
        context: ConsolidationContext,
    ) -> OpResult: ...
```

`OpResult` is the contract that preserves byte-identical behaviour
across the truncation-driven deletion subset (LLM strategy):

```python
@dataclass(frozen=True, slots=True)
class OpResult:
    content: str
    # subset of to_remove the summary actually represents; the
    # Composite deletes THIS, not the selector's full to_remove, so
    # entries dropped by LLM prompt truncation stay in the backend
    # for the next pass (preserves the existing contract exactly).
    represented: tuple[MemoryEntry, ...]
    # summary tags: ("consolidated",) for ConcatenationOp;
    # ("consolidated", "llm-synthesized"|"concat-fallback") for
    # LLMSynthesisOp; ("consolidated", "mode:extractive"|
    # "mode:abstractive") for DensityRoutingOp.
    tags: tuple[str, ...]
    # DualMode-only; empty tuple for every other op. Threading it
    # here keeps ConsolidationResult.mode_assignments byte-identical.
    mode_assignments: tuple[ArchivalModeAssignment, ...] = ()
```

`CompositeConsolidationStrategy(selector, op)` satisfies the existing
`ConsolidationStrategy` Protocol, so `MemoryConsolidationService` is
unchanged at the callsite. It runs the selector, then per group:
calls the op, stores `OpResult.content` with `OpResult.tags`, and
deletes exactly `OpResult.represented` (best-effort, matching the
existing per-strategy delete-failure tolerance).

### Implementations

Selector (one):

- `HighestRelevanceSelector(group_threshold)`: the shared
  category-group + relevance/recency selection, written once. Density
  classification is **not** selection -- in DualMode it decides which
  op processes a group, so it lives in the op, not the selector.

Operations:

- `ConcatenationOp`: truncated bullet concatenation (Simple's
  operation; also the shared fallback inside `LLMSynthesisOp`).
- `AbstractiveSummarizationOp`: wraps the existing
  `AbstractiveSummarizer` (per-entry LLM summary, `TaskGroup` fan-out).
- `ExtractivePreservationOp`: wraps the existing `ExtractivePreserver`
  (key-fact extraction).
- `LLMSynthesisOp`: LLM synthesis with trajectory context + the
  truncation accounting; concatenation fallback on LLM failure or
  empty result. Reports the truncation-survivor subset via
  `OpResult.represented`.
- `DensityRoutingOp(classifier, extractive_op, abstractive_op)`:
  majority-vote density classification over the group, then delegates
  to the extractive or abstractive op and stamps the
  `mode:<...>` tag + `mode_assignments`. Routing is intrinsic to this
  op; it composes the other two ops rather than duplicating them.

### The three existing strategies become composites

- Simple = `Composite(HighestRelevanceSelector, ConcatenationOp)`
- LLM = `Composite(HighestRelevanceSelector, LLMSynthesisOp)`
- DualMode = `Composite(HighestRelevanceSelector,
  DensityRoutingOp(classifier, ExtractivePreservationOp,
  AbstractiveSummarizationOp))`

No monolithic class is kept; no adapter wraps an old class. The three
public strategy names resolve to composite instances via the factory.

### Config + factory

`consolidation/config.py` gains a `ConsolidationStrategyType` `StrEnum`
discriminator (`SIMPLE`, `DUAL_MODE`, `LLM`) plus optional explicit
`selector` / `op` sub-discriminators for custom compositions.
`consolidation/factory.py::build_consolidation_strategy(config)` uses
the `StrEnum`-keyed `StrategyRegistry` from ADR-0002. The factory is
wired into `MemoryConsolidationService` construction; the strategy is
no longer hand-injected in app bootstrap.

## Migration mechanics

Ordered to make behaviour regressions impossible to land silently --
this is the highest-risk refactor in WP-4:

1. Byte-identical golden regression tests against the **current**
   monolithic Simple / DualMode / LLM (fixed inputs -> exact
   `ConsolidationResult`, including an oversized-batch LLM input that
   triggers `max_total_user_content_chars` truncation so the
   deletion-subset contract is pinned). These pass against today's
   code and must stay green through every later step.
2. Define `EntrySelector`, `ConsolidationOp`, `SelectionGroup`,
   `ConsolidationContext`, `OpResult`; add `HighestRelevanceSelector`,
   the four ops, `DensityRoutingOp`, and
   `CompositeConsolidationStrategy` as **new modules without touching
   the three existing strategy classes**.
3. Convert Simple to the composite; delete the monolith; golden green.
4. Convert DualMode (`DensityRoutingOp`); delete monolith; golden
   green (asserts `mode_assignments` byte-identical).
5. Convert LLM (`LLMSynthesisOp` with the `represented` subset
   contract); delete monolith; golden green (the truncation case
   guards this step).
6. Add `ConsolidationStrategyType` discriminator + the
   `StrEnum`-keyed `StrategyRegistry` factory (ADR-0002); wire it
   into `MemoryConsolidationService` construction; remove the
   hand-injection.
7. Reshape `tests/unit/memory/consolidation/test_*_strategy.py` into
   per-selector + per-op + composite tests; the step-1 golden tests
   remain as the byte-identical guard. Substantial; part of the
   deliverable, not a follow-up.
8. Update `docs/design/memory-consistency.md` with the axis-split
   section and `docs/reference/pluggable-subsystems.md` catalogue.

## Compat scope

None. The monolithic strategy classes are deleted in the same commit
that introduces the composite + selectors + ops. The public strategy
names survive only as factory outputs, not as classes.

## Alternatives considered

- **Protocols + factory now, keep the three classes monolithic
  internally behind the new interface.** Rejected (user decision): that
  is a deferred shim; the entanglement the ADR exists to remove would
  remain, just hidden behind an adapter.
- **Three axes (selector / grouper / op).** Rejected: grouping is part
  of selection in every existing strategy (group-then-keep); a separate
  grouper axis adds a degree of freedom no current or proposed strategy
  needs.
- **Keep the monolith.** Rejected: copy-pasted selection logic and
  duplicated fallback are the motivation; pre-alpha is the cheap window.

## Consequences

- `memory/consolidation/` gains selector and op modules; the three
  strategy files are removed.
- `MemoryConsolidationService` callsite is unchanged
  (`CompositeConsolidationStrategy` satisfies the old Protocol).
- Strategy selection becomes config-driven through the ADR-0002
  registry instead of bootstrap hand-injection.
- Significant one-time test reshape in
  `tests/unit/memory/consolidation/`.
- Out of scope: the memory backends, retrieval/injection strategies,
  archival policy.
