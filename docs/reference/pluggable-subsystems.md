# Pluggable Subsystems: Canonical Examples

On-demand reference. The rule in `CLAUDE.md` is: new cross-cutting subsystems follow a **protocol + strategy + factory + config discriminator** pattern, with safe defaults so the behaviour is opt-in. This page catalogues the canonical implementations.

## Pattern recap

- Define a `Protocol` interface.
- Ship concrete strategies that implement it.
- Register them in a factory keyed by a config discriminator.
- Plumb the active selection through frozen config.
- Ship safe defaults so nothing ever silently regresses.

## Registries

Three sibling registries replace the hand-rolled `if config.type == "...": ... elif ...` chains every factory used to carry. Each is immutable after construction (`MappingProxyType`-backed) and emits structured `registry.*` events for built / lookup / failure paths.

- `synthorg.core.registry.StrategyRegistry[T]`: generic strategy dispatch keyed by a `config.type` discriminator. Used by the four strategy-family factories (pruning, propagation, identity store, evolution proposer) plus the per-op rate-limit and inflight stores.
- `synthorg.persistence.registry.PersistenceBackendRegistry`: domain-specific dispatch keyed by `PersistenceConfig.backend`; preserves the lazy import of the optional `postgres` extra.
- `synthorg.memory.registry.MemoryBackendRegistry`: domain-specific dispatch keyed by `CompanyMemoryConfig.backend`; the composite-backend child loop reuses a separate "leaf" registry to keep the wiring acyclic.

Each subsystem still owns its config discriminator; the registries replace only the dispatch step that translates the discriminator into a constructor call.

## Canonical examples

### Classification pipeline

- `engine/classification/protocol.py`: `Detector`, `ScopedContextLoader`, `ClassificationSink`.
- `budget/coordination_config.py`: dispatcher.

### Verification graders

- `engine/quality/decomposer_protocol.py`: `CriteriaDecomposer`.
- `engine/quality/grader_protocol.py`: `RubricGrader`.
- `engine/quality/verification_factory.py` + `engine/quality/verification_config.py`.

### Chief of Staff

- `meta/chief_of_staff/protocol.py`: `OutcomeStore`, `ConfidenceAdjuster`, `OrgInflectionSink`, `AlertSink`.
- `meta/chief_of_staff/config.py`: discriminator.
- `meta/factory.py::build_confidence_adjuster()`.

### Analytics / telemetry

- `meta/telemetry/protocol.py`: `AnalyticsEmitter`, `AnalyticsCollector`, `RecommendationProvider`.
- `meta/telemetry/config.py`: discriminator.
- `meta/telemetry/factory.py::build_analytics_emitter()`.

### Rollout strategies

- `meta/rollout/clock.py`: `Clock`.
- `meta/rollout/roster.py`: `OrgRoster`.
- `meta/rollout/group_aggregator.py`: `GroupSignalAggregator`.
- `meta/rollout/inverse_dispatch.py`: `RollbackHandler` + 4 mutator protocols.
- `meta/factory.py::build_rollout_strategies()` + `build_rollback_executor()`.
- All plumbed through frozen `SelfImprovementConfig`, with safe defaults (`RealClock`, `NoOpOrgRoster`, null aggregator) so the behaviour is opt-in.

### API rate limits

- `api/rate_limits/protocol.py`: `SlidingWindowStore`.
- `api/rate_limits/in_memory.py`.
- `api/rate_limits/config.py::PerOpRateLimitConfig`: discriminator.
- `api/rate_limits/factory.py::build_sliding_window_store()`.

### API per-op concurrency

- `api/rate_limits/inflight_protocol.py`: `InflightStore`.
- `api/rate_limits/in_memory_inflight.py`.
- `api/rate_limits/inflight_config.py::PerOpConcurrencyConfig`: discriminator.
- `api/rate_limits/inflight_factory.py::build_inflight_store()`.
- `api/rate_limits/inflight_middleware.py::PerOpConcurrencyMiddleware` (Litestar middleware that reads `opt[per_op_concurrency]` from each route handler).

### Escalation queue

- `communication/conflict_resolution/escalation/protocol.py`: `EscalationQueueStore`, `DecisionProcessor`.
- In-memory / SQLite / Postgres implementations.
- `communication/conflict_resolution/escalation/config.py::EscalationQueueConfig`: discriminator.
- `communication/conflict_resolution/escalation/factory.py::build_escalation_queue_store()`.

### Assignment ranking and pool filtering

- `engine/assignment/protocol.py`: `TaskAssignmentStrategy` (the public Protocol; strategies are still selected by the `strategy` config string).
- `engine/assignment/pool_filter_protocol.py`: `CandidatePoolFilter` (pre-scoring narrowing of `available_agents`; `IdentityPoolFilter` is the default, `HierarchicalPoolFilter` narrows to subordinates of the task's delegator).
- `engine/assignment/ranker_protocol.py`: `CandidateRanker` (post-scoring ordering: `ScoreDescendingRanker`, `WorkloadAscendingRanker`, `CostDescendingRanker`, `AuctionBidRanker`).
- `engine/assignment/scoring_based.py::ScoringBasedAssignmentStrategy`: composes `(scorer, pool_filter, ranker)`. The five logical assignment strategies (`role_based`, `load_balanced`, `cost_optimized`, `auction`, `hierarchical`) are all `ScoringBasedAssignmentStrategy` instances with different filter/ranker pairs.
- `engine/assignment/registry.py::build_strategy_map()`: the factory; preserves the public string discriminators.

### HR pillar scoring

- `hr/evaluation/pillar_protocol.py`: `PillarScoringStrategy` (the public per-pillar Protocol).
- `hr/evaluation/metric_extractor_protocol.py`: `MetricExtractor` (per-pillar sub-metric extraction). Implementations live under `hr/evaluation/extractors/` (one file per pillar: intelligence, efficiency, resilience, governance, experience).
- `hr/evaluation/configurable_scorer.py::ConfigurablePillarScorer`: composes `(pillar, extractor)` to satisfy `PillarScoringStrategy`. Owns the shared "redistribute weights -> weighted-average -> clamp -> confidence -> log -> `PillarScore`" pipeline so the per-pillar extractors stay focused on data extraction.
- `hr/evaluation/evaluator.py::EvaluationService`: factory + orchestrator. Each pillar has a `_default_<pillar>()` method that returns `ConfigurablePillarScorer(pillar, <Pillar>MetricExtractor())`. Callers can substitute any compatible `PillarScoringStrategy` per pillar via the constructor's `<pillar>_strategy` keyword arguments.

## Services are a distinct pattern (not pluggable subsystems)

A **service** wraps one or more repositories to keep controllers thin and centralise audit logging, and MAY orchestrate multiple repositories (e.g. `WorkflowService` spans `workflow_definitions` + `workflow_versions`; `MemoryService` spans fine-tune checkpoints + runs + settings).

The Protocol + Strategy + Factory + Config pattern applies only to genuinely cross-cutting subsystems that ship multiple interchangeable implementations selectable at runtime. Services do not need that machinery because there is exactly one service per domain.
