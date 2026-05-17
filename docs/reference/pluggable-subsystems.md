# Pluggable Subsystems: Canonical Examples

On-demand reference. The rule in `CLAUDE.md` is: new cross-cutting subsystems follow a **protocol + strategy + factory + config discriminator** pattern, with safe defaults so the behaviour is opt-in. This page catalogues the canonical implementations.

## Pattern recap

- Define a `Protocol` interface.
- Ship concrete strategies that implement it.
- Register them in a factory keyed by a config discriminator.
- Plumb the active selection through frozen config.
- Ship safe defaults so nothing ever silently regresses.

## Registries

Three registry classes replace the hand-rolled `if config.type == "...": ... elif ...` chains every factory used to carry. Each is immutable after construction (`MappingProxyType`-backed) and emits structured `registry.*` events for built / lookup / failure paths.

- `synthorg.core.registry.StrategyRegistry[T]`: generic strategy dispatch keyed by a `config.type` discriminator. Used across the codebase by factories for pruning, propagation, identity store, evolution triggers, evolution proposers, execution loops, training source selectors, training curation, procedural capture, notification sinks, secret backends, sandbox backends, per-op rate-limit stores, per-op inflight stores, and memory-consolidation strategies (selector + op composite, ADR-0005).
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

- `meta/rollout/roster.py`: `OrgRoster`.
- `meta/rollout/group_aggregator.py`: `GroupSignalAggregator`.
- `meta/rollout/inverse_dispatch.py`: `RollbackHandler` + 4 mutator protocols.
- `meta/factory.py::build_rollout_strategies()` + `build_rollback_executor()`.
- All plumbed through frozen `SelfImprovementConfig`, with safe defaults (`SystemClock` from `synthorg.core.clock`, `NoOpOrgRoster`, null aggregator) so the behaviour is opt-in.

### Rollback mutators

Concrete implementations of the four mutator protocols live under `meta/rollout/mutators/`:

- `SettingsServiceConfigMutator` (`mutators/config_mutator.py`): backs `ConfigMutator` with `SettingsService.set`. Dotted target (`"<namespace>.<key>"`); `read_only_post_init` settings surface as `RollbackMutationDeniedError`.
- `PrincipleOverridePromptMutator` (`mutators/prompt_mutator.py`): backs `PromptMutator` with `PrincipleOverrideRepository`. Persists override rows that `engine/strategy/principles.py::load_pack` consults on principle resolution. Schema: `persistence/sqlite/revisions/20260513000002_principle_overrides.sql` (and Postgres twin).
- `RoutedArchitectureMutator` (`mutators/architecture_mutator.py`): backs `ArchitectureMutator` with a per-target-type adapter registry. Target format `"<type>:<id>"` (or `"<type>:<id>:<sub_id>"`); operators register adapters per type (`role`, `department`, `workflow`, etc.) without touching the executor. Unknown prefixes raise `UnknownArchitectureTargetError`.
- `WorkspaceCodeMutator` (`mutators/code_mutator.py`): backs `CodeMutator` with atomic filesystem writes (`tempfile.mkstemp` + `Path.replace`) inside a workspace bounded by `PathValidator` so `revert_code` cannot escape via traversal.

Domain errors live at `meta/errors.py::RollbackMutationDeniedError` (409) and `UnknownArchitectureTargetError`. Wire-up assembles via `meta/factory.py::build_rollback_executor(config_mutator=..., prompt_mutator=..., architecture_mutator=..., code_mutator=...)` and stays opt-in: operators construct the executor in their own startup path when they want the self-improvement loop active.

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

### Memory injection strategy

- `memory/injection.py`: `MemoryInjectionStrategy` Protocol + `InjectionStrategy` discriminator (`CONTEXT`, `TOOL_BASED`, `SELF_EDITING`).
- Concrete implementations: `memory/context_injection.py::ContextInjectionStrategy`, `memory/tool_based.py::ToolBasedInjectionStrategy`, `memory/self_editing.py::SelfEditingMemoryStrategy`.
- `memory/retrieval_config.py::MemoryRetrievalConfig.strategy`: discriminator.
- `memory/injection_factory.py::build_memory_injection_strategy()`: match-based dispatch with `assert_never` exhaustiveness.

### Engine recovery strategy

- `engine/recovery.py`: `RecoveryStrategy` Protocol + `FailAndReassignStrategy`.
- `engine/checkpoint/strategy.py::CheckpointRecoveryStrategy`: resume-from-checkpoint sibling.
- `engine/recovery_config.py::EngineRecoveryConfig.strategy` (`RecoveryStrategyType`): discriminator.
- `engine/recovery_factory.py::build_recovery_strategy()`: match-based dispatch; `RecoveryConfigError` surfaces missing `checkpoint_repo` / `checkpoint_config` at boot rather than at recovery time.

### Memory consolidation strategy (axis split, ADR-0005)

- `memory/consolidation/axis.py`: `EntrySelector` + `ConsolidationOp` Protocols; `SelectionGroup` / `ConsolidationContext` / `OpResult` contracts.
- Selector: `memory/consolidation/selectors.py::HighestRelevanceSelector` (shared by all three shipped strategies).
- Ops: `memory/consolidation/ops.py` (`ConcatenationOp`, `DensityRoutingOp`, `ExtractivePreservationOp`, `AbstractiveSummarizationOp`) + `memory/consolidation/llm_op.py::LLMSynthesisOp`.
- `memory/consolidation/composite.py::CompositeConsolidationStrategy`: selector + op aggregator (`parallel=True` for LLM cross-group `TaskGroup` fan-out).
- `memory/consolidation/config.py::ConsolidationStrategyType` (`SIMPLE` / `DUAL_MODE` / `LLM`): discriminator.
- `memory/consolidation/factory.py::build_consolidation_strategy()`: `StrEnum`-keyed `StrategyRegistry` dispatch; `MemoryConfigError` surfaces missing op-specific deps at construction.

### Conflict detector

- `communication/meeting/protocol.py`: `ConflictDetector` Protocol.
- `communication/meeting/conflict_detection.py`: six concrete implementations (`KeywordConflictDetector`, `StructuredComparisonDetector`, `LlmJudgeDetector`, `EmbeddingSimilarityDetector`, `HybridDetector`, `AutoDetector`).
- `communication/meeting/enums.py::ConflictDetectorType`: discriminator.
- `communication/meeting/factory.py::build_conflict_detector()`: `StrategyRegistry` dispatch.

### Trust strategy (conditional instantiation)

- `security/trust/protocol.py`: `TrustStrategy` Protocol.
- `security/trust/{weighted,per_category,milestone}_strategy.py`: three real implementations.
- `security/trust/config.py::TrustConfig.strategy` (`TrustStrategyType` with a `DISABLED` value): discriminator.
- `security/trust/factory.py::build_trust_strategy()`: registry dispatch that returns `None` for `DISABLED` so callers skip `TrustService` construction entirely (instead of wiring a no-op strategy).

### Ontology versioning (inverted backend dependency)

- `ontology/versioning.py`: pure `EntityDefinition` snapshot deserializers; carries no backend imports.
- `persistence/sqlite/ontology_versioning.py::create_ontology_versioning()`: SQLite-side factory.
- `persistence/postgres/ontology_versioning.py::create_postgres_ontology_versioning()`: Postgres-side factory.
- Each backend's lifecycle helper composes the matching factory at startup, so the dependency arrow points `persistence -> ontology`, never the reverse.

### Backup handler registry (backend-pluggable)

- `backup/handlers/protocol.py`: `ComponentHandler` Protocol.
- `backup/handlers/sqlite_persistence.py::SQLitePersistenceComponentHandler`, `backup/handlers/postgres_persistence.py::PostgresPersistenceComponentHandler`, `backup/handlers/memory.py::MemoryComponentHandler`, `backup/handlers/config_handler.py::ConfigComponentHandler`.
- `backup/registry.py::PERSISTENCE_BACKUP_HANDLER_REGISTRY`: `StrategyRegistry` keyed on `config.persistence.backend` ("sqlite" / "postgres").
- `backup/factory.py::build_backup_handlers()`: dispatches per `BackupComponent` and uses the registry for the persistence handler.

## Services are a distinct pattern (not pluggable subsystems)

A **service** wraps one or more repositories to keep controllers thin and centralise audit logging, and MAY orchestrate multiple repositories (e.g. `WorkflowService` spans `workflow_definitions` + `workflow_versions`; `MemoryService` spans fine-tune checkpoints + runs + settings).

The Protocol + Strategy + Factory + Config pattern applies only to genuinely cross-cutting subsystems that ship multiple interchangeable implementations selectable at runtime. Services do not need that machinery because there is exactly one service per domain.
