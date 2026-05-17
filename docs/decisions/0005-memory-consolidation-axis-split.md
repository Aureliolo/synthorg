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

```python
@runtime_checkable
class EntrySelector(Protocol):
    async def select(
        self, entries: tuple[MemoryEntry, ...], *, agent_id: NotBlankStr
    ) -> SelectionResult: ...
        # SelectionResult: tuple of groups, each (kept, to_consolidate)

@runtime_checkable
class ConsolidationOp(Protocol):
    async def consolidate(
        self, removed: tuple[MemoryEntry, ...], *, context: OpContext
    ) -> ConsolidatedEntry: ...
```

`CompositeConsolidationStrategy(selector, op)` satisfies the existing
`ConsolidationStrategy` Protocol, so `MemoryConsolidationService` is
unchanged at the callsite.

### Implementations

Selectors:

- `HighestRelevanceSelector`: group by category, keep top relevance,
  recency tiebreak (the Simple/LLM selection logic, written once).
- `DensityClassifierSelector`: group by category, sparse/dense
  majority-vote classification carried on `SelectionResult` group
  metadata.

Operations:

- `ConcatenationOp`: truncated concatenation (Simple's operation; the
  shared fallback for `LLMSynthesisOp`).
- `AbstractiveSummarizationOp`: LLM abstractive summary.
- `ExtractivePreservationOp`: key-fact extraction.
- `LLMSynthesisOp`: LLM synthesis with trajectory context;
  concatenation fallback on LLM failure or empty result.
- `DensityRoutingOp`: routes dense groups to `ExtractivePreservationOp`
  and sparse groups to `AbstractiveSummarizationOp`. Density routing is
  intrinsic to this operation (it consumes the selector's density
  metadata), so it stays one op rather than being pushed into the
  selector.

### The three existing strategies become composites

- Simple = `Composite(HighestRelevanceSelector, ConcatenationOp)`
- LLM = `Composite(HighestRelevanceSelector, LLMSynthesisOp)`
- DualMode = `Composite(DensityClassifierSelector, DensityRoutingOp)`

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

1. Define `EntrySelector`, `ConsolidationOp`, `SelectionResult`,
   `OpContext`, `ConsolidatedEntry` (reuse the existing result models
   where shapes already match).
2. Extract selectors and ops from the three strategy bodies; delete the
   monolithic strategy classes.
3. Add `CompositeConsolidationStrategy`; add the discriminator + the
   factory (registry-backed).
4. Wire the factory into service construction; remove the hand-injection.
5. Reshape `tests/unit/memory/consolidation/test_*_strategy.py` into
   (a) per-selector tests, (b) per-op tests, (c) composite integration
   tests asserting Simple / DualMode / LLM produce byte-identical
   output to the pre-split behaviour on golden inputs. This test
   reshape is substantial and is part of the deliverable, not a
   follow-up.
6. Update `docs/design/memory-consistency.md` with the axis-split
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
