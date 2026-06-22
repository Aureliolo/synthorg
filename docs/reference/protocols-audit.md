# Protocols Audit (2026-05-10)

This audit inventories every `Protocol` class under `src/synthorg/`, captures usage signals, and classifies each into a recommended action. The follow-up cleanup work is filed as separate issues; this document is the reference baseline they hyperlink back to.

Tracker: [#1850](https://github.com/Aureliolo/synthorg/issues/1850) sub-task **I** (audit only). The cleanup itself is intentionally **out of scope** of the PR that ships this document.

## Methodology

Generated mechanically by [`scripts/protocol_audit.py`](../../scripts/protocol_audit.py); regenerate with `uv run python scripts/protocol_audit.py` from the repo root against the commit recorded by the date in the title above. The script walks `src/synthorg/` for every `class X(...Protocol...):` definition and records:

- **Path + line** of the `class X(Protocol)` line.
- **`@runtime_checkable`** decorator presence.
- **Direct-inheritance count** (`impl`): number of files matching `class \w+\(.*\b<Name>\b` in `src/synthorg/`. Captures the explicit-subclass case; **does not** capture pure structural-subtyping implementations (Python's default for Protocols), so `impl=0` is neutral, not a removal signal on its own.
- **Test-usage count** (`testuse`): occurrences of the protocol name in `tests/`. A protocol with `testuse=0` is rarely consumed by name in the test surface.

The counts are deliberately approximate: they're a triage tool, not a removal lockbox. Every `REMOVE` recommendation in the per-area tables below is a **flag**, not a pre-approved deletion: each follow-up issue must re-verify usage at cleanup time.

**Important caveat on `impl=0`.** The script's regex matches only explicit `class X(ProtocolName):` inheritance; it cannot detect Pythonic *structural* implementations (classes that satisfy the Protocol by shape without inheriting), factory dispatch dicts that return Protocol-typed values, or default-impl injection patterns. In practice, most flagged `REMOVE` rows turned out to be real plug-in surfaces once re-grepped at cleanup time. The 2026-05-11 cleanup (issue #1864) hand-reviewed every REMOVE row and reclassified 44 of 46 candidates back to KEEP after finding structural impls in sibling files, factory wiring, or vendor-agnostic injection design. See the **Post-cleanup status (2026-05-11)** section below.

## Classification rules

- **KEEP**: leave the protocol unchanged. Applies when:
  - `impl ≥ 1` (explicit subclass present), or
  - the name ends in a known plug-in suffix (`Strategy`, `Backend`, `Provider`, `Repository`, `Repo`, `Store`, `Bus`, `Subscriber`, `Sink`, `Handler`, `Verifier`, `Resolver`, `Detector`, `Adjuster`, `Mutator`, `Adapter`, `Authenticator`, `Capability`, `Hook`, `Trigger`, `Guard`, `Policy`, `Engine`, `Plugin`, `Aggregator`, `Selector`, `Reformulator`, `Compressor`, `Reranker`, `Retriever`, `Worker`, `Estimator`, `Classifier`, `Grader`, `Decomposer`, `Calculator`, `Formatter`, `Scorer`, `Source`, `Loader`, `Reader`, `Manager`, `Dispatcher`), or
  - explicit factory-mapping registration (e.g. `_*_FACTORIES` discriminated MappingProxyType), or
  - `testuse ≥ 3` (regularly consumed by name in test code, typically as a typed mock seam or fixture parameter).

- **MAKE-RUNTIME-CHECKABLE**: flag for promotion to `@runtime_checkable`. Applies when the protocol is used at a runtime `isinstance` site or registered through a factory mapping that should validate at registration. The audit lists these as **review-at-cleanup-time**: each candidate needs a quick grep for `isinstance(.*<Name>)` to confirm the runtime check exists. Without that confirmation, leave as `KEEP`.

- **REMOVE**: flag for removal. Applies when:
  - `impl = 0` AND `testuse = 0` AND the name is **not** in a public extension surface (no plug-in suffix, no factory registration), and
  - the only consumer is a single private module that could absorb the concrete shape directly.

- **REVIEW**: needs human eyeballs. Applies to the handful of `_PrivatePrefixed` typing seams whose intent isn't obvious from name alone.

## Summary

The 2026-05-10 snapshot counts in the original table were aggregated by hand and undercounted both the REMOVE and REVIEW rows. The table below reflects (a) the per-area row totals as they actually appear in the snapshot tables, and (b) the post-2026-05-11 cleanup outcome captured in the "Post-cleanup status" section.

| Total | Snapshot (2026-05-10) | After #1864 (2026-05-11) | After #1865 (2026-05-11) |
|---|---|---|---|
| Protocol classes inventoried | 250 | 248 (2 deleted) | 241 (7 more deleted) |
| KEEP | 193 | 237 (44 audit re-flagged, plus pre-existing 193) | 241 (5 `_PrivatePrefixed` retained with rationale; 1 fold-deletion drops conflict_detection.py's KEEP) |
| MAKE-RUNTIME-CHECKABLE | 0 (no `isinstance` sites surveyed) | 0 | 0 |
| REMOVE candidates (flag-only; must be re-verified at cleanup time) | 46 | 0 (2 deleted, 44 reclassified KEEP) | 0 |
| REVIEW (`_PrivatePrefixed` typing seams; intent inspection needed) | 11 | 11 (unchanged; #1865 owns these) | 0 (12 protocol names across the 11 snapshot rows: 5 retained with rationale, 5 deleted, 2 fold-deleted) |

`@runtime_checkable` decoration: 209 of 250 (84%). The 41 without the decorator are listed inline below; absence is not a defect on its own; only a few need it.

## Per-area classification

Tables sort by recommendation (REMOVE first, then REVIEW, then KEEP). `rc` is `1` when `@runtime_checkable` is present.

### `src/synthorg/api/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| api/auto_wire.py | 93 | `BuildDispatcherFn` | 0 | 0 | 0 | REMOVE | Internal typing seam; auto-wire callsite can use `Callable[..., ...]` directly. |
| api/lifecycle.py | 86 | `_AsyncStartStop` | 0 | 0 | 0 | REVIEW | Private typing seam; verify whether lifecycle helpers need it as a structural surface. |
| api/rate_limits/inflight_protocol.py | 18 | `InflightStore` | 1 | 1 | 0 | KEEP | Pluggable `Store` (factory-registered). |
| api/rate_limits/protocol.py | 49 | `SlidingWindowStore` | 1 | 1 | 0 | KEEP | Pluggable `Store` (factory-registered). |

### `src/synthorg/approval/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| approval/protocol.py | 19 | `ApprovalStoreProtocol` | 1 | 0 | 29 | KEEP | Plug-in `Store`; high test usage. |
| approval/protocol.py | 71 | `SyncResettableApprovalStore` | 1 | 0 | 4 | KEEP | Capability-marker variant of the store. |

### `src/synthorg/backup/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| backup/handlers/protocol.py | 16 | `ComponentHandler` | 1 | 0 | 1 | KEEP | Plug-in `Handler`. |

### `src/synthorg/budget/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| budget/call_classifier.py | 70 | `CallClassificationStrategy` | 1 | 0 | 0 | KEEP | Plug-in `Strategy`. |
| budget/coordination_collector.py | 86 | `SimilarityComputer` | 1 | 0 | 3 | KEEP | Plug-in `Computer` (test-validated). |

### `src/synthorg/client/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| client/human_queue.py | 58 | `HumanInputQueue` | 1 | 0 | 3 | KEEP | Plug-in `Queue`. |
| client/protocols.py | 23 | `ClientInterface` | 1 | 0 | 17 | KEEP | Public extension surface. |
| client/protocols.py | 68 | `RequirementGenerator` | 1 | 0 | 14 | KEEP | Plug-in `Generator`. |
| client/protocols.py | 91 | `FeedbackStrategy` | 1 | 0 | 12 | KEEP | Plug-in `Strategy`. |
| client/protocols.py | 114 | `ReportStrategy` | 1 | 0 | 9 | KEEP | Plug-in `Strategy`. |
| client/protocols.py | 137 | `ClientPoolStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |
| client/protocols.py | 161 | `EntryPointStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |

### `src/synthorg/communication/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| communication/conflict_resolution/escalation/notify.py | 56 | `EscalationNotifySubscriber` | 1 | 0 | 0 | KEEP | Plug-in `Subscriber`; factory-registered (`build_escalation_notify_subscriber`). |
| communication/conflict_resolution/escalation/protocol.py | 175 | `DecisionProcessor` | 1 | 0 | 0 | KEEP | Pluggable strategy; updated by Phase G. |
| communication/conflict_resolution/protocol.py | 70 | `JudgeEvaluator` | 0 | 0 | 0 | REMOVE | No subclasses, no test references; verify single consumer can take a concrete type. |
| communication/event_stream/consumer.py | 15 | `EventStreamConsumer` | 1 | 0 | 0 | REMOVE | No usages; one-consumer seam. |
| communication/meeting/participant.py | 56 | `ParticipantResolver` | 1 | 0 | 0 | REMOVE | Same name lives in `meeting/protocol.py`; one of the two is dead. |
| communication/bus/persistence.py | 33 | `HistoryAccessor` | 1 | 0 | 2 | KEEP | Plug-in `Accessor`. |
| communication/bus_protocol.py | 21 | `MessageBus` | 1 | 0 | 54 | KEEP | Core pluggable subsystem. |
| communication/conflict_resolution/escalation/protocol.py | 26 | `EscalationQueueStore` | 1 | 3 | 7 | KEEP | Three concrete stores (in-memory, sqlite, postgres). |
| communication/conflict_resolution/escalation/protocol.py | 149 | `CrossInstanceNotifyCapableStore` | 1 | 0 | 5 | KEEP | Capability marker. |
| communication/conflict_resolution/protocol.py | 30 | `ConflictResolver` | 0 | 0 | 11 | KEEP | Plug-in `Resolver`. |
| communication/handler.py | 21 | `MessageHandler` | 1 | 0 | 8 | KEEP | Plug-in `Handler`. |
| communication/meeting/conflict_detection.py | 39 | `ConflictDetector` | 1 | 0 | 8 | KEEP | Plug-in `Detector`. |
| communication/meeting/protocol.py | 41 | `ConflictDetector` | 1 | 0 | 8 | REVIEW | Duplicate of the name in `conflict_detection.py`; one of the two should fold into the other. |
| communication/meeting/protocol.py | 63 | `MeetingProtocol` | 1 | 0 | 19 | KEEP | Public extension surface. |

### `src/synthorg/core/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| core/clock.py | 52 | `Clock` | 1 | 0 | 12 | KEEP | Test seam (`FakeClock`). |
| core/state_machine.py | 36 | `_HasValue` | 0 | 0 | 0 | REVIEW | Private typing seam. |

### `src/synthorg/engine/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| engine/evolution/guards/shadow_protocol.py | 69 | `ShadowTaskProvider` | 0 | 0 | 0 | REMOVE | One-consumer seam. |
| engine/evolution/guards/shadow_protocol.py | 100 | `ShadowAgentRunner` | 0 | 0 | 0 | REMOVE | One-consumer seam. |
| engine/quality/classifier.py | 44 | `StepQualityClassifier` | 1 | 0 | 0 | REMOVE | No usages outside the module. |
| engine/quality/graders/heuristic.py | 27 | `_HeuristicGraderBridge` | 0 | 0 | 0 | REVIEW | Private bridge typing seam. |
| engine/review/protocol.py | 14 | `ReviewStage` | 1 | 0 | 0 | REMOVE | No usages. |
| engine/routing/scorer.py | 40 | `_RoutingScorerBridge` | 0 | 0 | 0 | REVIEW | Private bridge typing seam. |
| engine/strategy/confidence.py | 21 | `ConfidenceFormatter` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no test/inheritance signal. |
| engine/strategy/context.py | 60 | `StrategicContextProvider` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no test/inheritance signal. |
| engine/strategy/impact.py | 57 | `ImpactScorer` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no test/inheritance signal. |
| engine/strategy/tiers.py | 21 | `CostTierResolver` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no test/inheritance signal. |
| engine/coordination/dispatcher_types.py | 60 | `TopologyDispatcher` | 1 | 0 | 3 | KEEP | Plug-in `Dispatcher`. |
| engine/coordination/dispatcher_types.py | (extras) | `_ExecutionResultLike`, `_AgentRunResultLike` | 0 | 0 | 0 | REVIEW | Private structural seams; verify intent. |
| engine/assignment/pool_filter_protocol.py | 78 | `CandidatePoolFilter` | 1 | 0 | 4 | KEEP | Plug-in `Filter`. |
| engine/assignment/protocol.py | 16 | `TaskAssignmentStrategy` | 1 | 0 | 7 | KEEP | Plug-in `Strategy`. |
| engine/assignment/ranker_protocol.py | 56 | `CandidateRanker` | 1 | 0 | 6 | KEEP | Plug-in `Ranker`. |
| engine/classification/protocol.py | 91 | `Detector` | 1 | 0 | 24 | KEEP | Plug-in `Detector`. |
| engine/classification/protocol.py | 125 | `ScopedContextLoader` | 1 | 0 | 3 | KEEP | Plug-in `Loader`. |
| engine/classification/protocol.py | 148 | `ClassificationSink` | 1 | 1 | 4 | KEEP | Plug-in `Sink`. |
| engine/classification/taxonomy_store_protocol.py | 30 | `ErrorTaxonomyStore` | 1 | 0 | 3 | KEEP | Plug-in `Store`. |
| engine/decomposition/protocol.py | 14 | `DecompositionStrategy` | 1 | 1 | 6 | KEEP | Plug-in `Strategy`. |
| engine/evolution/protocols.py | 61 | `EvolutionTrigger` | 1 | 0 | 4 | KEEP | Plug-in `Trigger`. |
| engine/evolution/protocols.py | 92 | `AdaptationProposer` | 1 | 0 | 3 | KEEP | Plug-in `Proposer`. |
| engine/evolution/protocols.py | 124 | `AdaptationGuard` | 1 | 0 | 15 | KEEP | Plug-in `Guard`. |
| engine/evolution/protocols.py | 152 | `AdaptationAdapter` | 1 | 0 | 5 | KEEP | Plug-in `Adapter`. |
| engine/identity/store/protocol.py | 18 | `IdentityVersionStore` | 1 | 0 | 4 | KEEP | Plug-in `Store`. |
| engine/intake/protocol.py | 14 | `IntakeStrategy` | 1 | 0 | 3 | KEEP | Plug-in `Strategy`. |
| engine/loop_protocol.py | 274 | `ExecutionLoop` | 1 | 0 | 9 | KEEP | Public extension surface. |
| engine/middleware/coordination_constraints.py | 203 | `CoordinationReplanHook` | 1 | 0 | 1 | KEEP | Plug-in `Hook`. |
| engine/middleware/coordination_protocol.py | 124 | `CoordinationMiddleware` | 1 | 0 | 9 | KEEP | Public middleware surface. |
| engine/middleware/orchestrator_strategy.py | 23 | `OrchestratorStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |
| engine/middleware/protocol.py | 50 | `AgentMiddleware` | 1 | 0 | 11 | KEEP | Public middleware surface. |
| engine/quality/decomposer_protocol.py | 11 | `CriteriaDecomposer` | 1 | 0 | 3 | KEEP | Plug-in `Decomposer`. |
| engine/quality/grader_protocol.py | 15 | `RubricGrader` | 1 | 0 | 3 | KEEP | Plug-in `Grader`. |
| engine/recovery.py | 304 | `RecoveryStrategy` | 1 | 0 | 7 | KEEP | Plug-in `Strategy`. |
| engine/resource_lock.py | 24 | `ResourceLock` | 1 | 0 | 3 | KEEP | Public lock contract. |
| engine/session.py | 133 | `EventReader` | 1 | 0 | 7 | KEEP | Plug-in `Reader`. |
| engine/shutdown.py | 97 | `ShutdownStrategy` | 1 | 0 | 12 | KEEP | Plug-in `Strategy`. |
| engine/stagnation/protocol.py | 15 | `StagnationDetector` | 1 | 0 | 3 | KEEP | Plug-in `Detector`. |
| engine/strategy/lens_assignment.py | 17 | `LensAssigner` | 1 | 0 | 4 | KEEP | Plug-in `Assigner`. |
| engine/strategy/premortem.py | 84 | `PremortemExecutor` | 1 | 0 | 4 | KEEP | Plug-in `Executor`. |
| engine/token_estimation.py | 15 | `PromptTokenEstimator` | 1 | 0 | 4 | KEEP | Plug-in `Estimator`. |
| engine/workflow/ceremony_strategy.py | 34 | `CeremonySchedulingStrategy` | 1 | 0 | 20 | KEEP | Plug-in `Strategy`. |
| engine/workflow/velocity_calculator.py | 24 | `VelocityCalculator` | 1 | 0 | 9 | KEEP | Plug-in `Calculator`. |
| engine/workspace/protocol.py | 14 | `WorkspaceIsolationStrategy` | 1 | 0 | 9 | KEEP | Plug-in `Strategy`. |
| engine/workspace/semantic_analyzer.py | 35 | `SemanticAnalyzer` | 1 | 0 | 14 | KEEP | Plug-in `Analyzer`. |

### `src/synthorg/hr/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| hr/archival_protocol.py | 39 | `MemoryArchivalStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer; verify before delete. |
| hr/evaluation/pillar_protocol.py | 17 | `PillarScoringStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/performance/collaboration_protocol.py | 17 | `CollaborationScoringStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/performance/trend_protocol.py | 17 | `TrendDetectionStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/performance/window_protocol.py | 19 | `MetricsWindowStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/promotion/approval_protocol.py | 18 | `PromotionApprovalStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/promotion/criteria_protocol.py | 16 | `PromotionCriteriaStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/promotion/model_mapping_protocol.py | 15 | `ModelMappingStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/pruning/policy.py | 32 | `PruningPolicy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/reassignment_protocol.py | 14 | `TaskReassignmentStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| hr/evaluation/external_benchmark_protocol.py | 19 | `ExternalBenchmark` | 1 | 0 | 1 | KEEP | Plug-in `Benchmark`. |
| hr/evaluation/metric_extractor_protocol.py | 156 | `MetricExtractor` | 1 | 0 | 9 | KEEP | Plug-in `Extractor`. |
| hr/performance/inflection_protocol.py | 55 | `InflectionSink` | 1 | 0 | 8 | KEEP | Plug-in `Sink`. |
| hr/performance/quality_protocol.py | 18 | `QualityScoringStrategy` | 1 | 0 | 1 | KEEP | Plug-in `Strategy`. |
| hr/persistence_protocol.py | 22 | `LifecycleEventRepository` | 1 | 0 | 4 | KEEP | Plug-in `Repository`. |
| hr/persistence_protocol.py | 62 | `TaskMetricRepository` | 1 | 0 | 4 | KEEP | Plug-in `Repository`. |
| hr/persistence_protocol.py | 100 | `CollaborationMetricRepository` | 1 | 0 | 4 | KEEP | Plug-in `Repository`. |
| hr/scaling/protocols.py | 20 | `ScalingStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |
| hr/scaling/protocols.py | 53 | `ScalingSignalSource` | 1 | 0 | 3 | KEEP | Plug-in `Source`. |
| hr/scaling/protocols.py | 81 | `ScalingTrigger` | 1 | 0 | 3 | KEEP | Plug-in `Trigger`. |
| hr/scaling/protocols.py | 107 | `ScalingGuard` | 1 | 0 | 3 | KEEP | Plug-in `Guard`. |
| hr/training/protocol.py | 22 | `ContentExtractor` | 1 | 0 | 3 | KEEP | Plug-in `Extractor`. |
| hr/training/protocol.py | 55 | `SourceSelector` | 1 | 0 | 3 | KEEP | Plug-in `Selector`. |
| hr/training/protocol.py | 85 | `CurationStrategy` | 1 | 0 | 3 | KEEP | Plug-in `Strategy`. |
| hr/training/protocol.py | 120 | `TrainingGuard` | 1 | 0 | 5 | KEEP | Plug-in `Guard`. |

### `src/synthorg/integrations/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| integrations/connections/protocol.py | 10 | `ConnectionAuthenticator` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| integrations/health/protocol.py | 12 | `ConnectionHealthCheck` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| integrations/tunnel/protocol.py | 7 | `TunnelProvider` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| integrations/mcp_catalog/installations.py | 39 | `McpInstallationRepository` | 1 | 0 | 3 | KEEP | Plug-in `Repository` (also defined in persistence/, see notes). |
| integrations/webhooks/definition_store_protocol.py | 17 | `WebhookDefinitionStore` | 1 | 0 | 2 | KEEP | Plug-in `Store`. |
| integrations/webhooks/verifiers/protocol.py | 7 | `SignatureVerifier` | 1 | 0 | 3 | KEEP | Plug-in `Verifier`. |

### `src/synthorg/memory/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| memory/procedural/capture/protocol.py | 19 | `CaptureStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| memory/procedural/propagation/protocol.py | 18 | `PropagationStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| memory/procedural/pruning/protocol.py | 15 | `PruningStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| memory/capabilities.py | 13 | `MemoryCapabilities` | 1 | 0 | 8 | KEEP | Plug-in `Capabilities`. |
| memory/consolidation/archival.py | 15 | `ArchivalStore` | 1 | 0 | 6 | KEEP | Plug-in `Store`. |
| memory/consolidation/compressor.py | 51 | `ExperienceCompressor` | 1 | 0 | 4 | KEEP | Plug-in `Compressor`. |
| memory/consolidation/strategy.py | 15 | `ConsolidationStrategy` | 1 | 0 | 6 | KEEP | Plug-in `Strategy`. |
| memory/embedding/fine_tune_orchestrator.py | 58 | `ChannelsPlugin` | 0 | 0 | 31 | KEEP | Plug-in extension point (high test usage). |
| memory/filter.py | 28 | `MemoryFilterStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |
| memory/injection.py | 52 | `TokenEstimator` | 1 | 0 | 5 | KEEP | Plug-in `Estimator`. |
| memory/injection.py | 97 | `MemoryInjectionStrategy` | 1 | 0 | 12 | KEEP | Plug-in `Strategy`. |
| memory/org/protocol.py | 20 | `OrgMemoryBackend` | 1 | 0 | 6 | KEEP | Plug-in `Backend`. |
| memory/protocol.py | 20 | `MemoryBackend` | 1 | 0 | 107 | KEEP | Core pluggable subsystem. |
| memory/reformulation.py | 49 | `QueryReformulator` | 1 | 0 | 3 | KEEP | Plug-in `Reformulator`. |
| memory/reformulation.py | 71 | `SufficiencyChecker` | 1 | 0 | 3 | KEEP | Plug-in `Checker`. |
| memory/retrieval/protocol.py | 18 | `RetrievalWorker` | 1 | 0 | 8 | KEEP | Plug-in `Worker`. |
| memory/retrieval/protocol.py | 44 | `HierarchicalRetriever` | 1 | 0 | 6 | KEEP | Plug-in `Retriever`. |
| memory/retrieval/reranking/protocol.py | 16 | `QuerySpecificReranker` | 1 | 0 | 6 | KEEP | Plug-in `Reranker`. |
| memory/shared.py | 18 | `SharedKnowledgeStore` | 1 | 0 | 11 | KEEP | Plug-in `Store`. |

### `src/synthorg/meta/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| meta/chief_of_staff/protocol.py | 22 | `OutcomeStore` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/chief_of_staff/protocol.py | 84 | `ConfidenceAdjuster` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/chief_of_staff/protocol.py | 121 | `OrgInflectionSink` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/chief_of_staff/protocol.py | 139 | `AlertSink` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/protocol.py | 346 | `CIValidator` | 1 | 0 | 0 | REMOVE | No usages. |
| meta/telemetry/protocol.py | 21 | `AnalyticsEmitter` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/telemetry/protocol.py | 66 | `AnalyticsCollector` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/telemetry/protocol.py | 105 | `RecommendationProvider` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| meta/appliers/architecture_applier.py | 79 | `ArchitectureApplierContext` | 1 | 0 | 3 | KEEP | Plug-in context. |
| meta/appliers/prompt_applier.py | 35 | `PromptApplierContext` | 1 | 0 | 3 | KEEP | Plug-in context. |
| meta/evolution/outcome_store_protocol.py | 31 | `EvolutionOutcomeStore` | 1 | 0 | 3 | KEEP | Plug-in `Store`. |
| meta/mcp/handler_protocol.py | 19 | `ToolHandler` | 0 | 0 | 2 | KEEP | Public MCP extension surface (<!--RS:mcp_tools-->244<!--/RS--> tool handlers); add `@runtime_checkable` candidate. |
| meta/protocol.py | 38 | `SignalAggregator` | 1 | 0 | 4 | KEEP | Plug-in `Aggregator`. |
| meta/protocol.py | 78 | `SignalRule` | 1 | 0 | 6 | KEEP | Plug-in `Rule`. |
| meta/protocol.py | 108 | `ImprovementStrategy` | 1 | 0 | 4 | KEEP | Plug-in `Strategy`. |
| meta/protocol.py | 140 | `ProposalGuard` | 1 | 0 | 5 | KEEP | Plug-in `Guard`. |
| meta/protocol.py | 168 | `ProposalApplier` | 1 | 0 | 5 | KEEP | Plug-in `Applier`. |
| meta/protocol.py | 210 | `RolloutStrategy` | 1 | 0 | 5 | KEEP | Plug-in `Strategy`. |
| meta/protocol.py | 243 | `RegressionDetector` | 1 | 0 | 6 | KEEP | Plug-in `Detector`. |
| meta/protocol.py | 276 | `GitHubAPI` | 1 | 0 | 1 | KEEP | Public-API surface. |
| meta/rollout/group_aggregator.py | 71 | `GroupSignalAggregator` | 1 | 0 | 3 | KEEP | Plug-in `Aggregator`. |
| meta/rollout/inverse_dispatch.py | 38 | `RollbackHandler` | 1 | 0 | 10 | KEEP | Plug-in `Handler`. |
| meta/rollout/inverse_dispatch.py | 55 | `ConfigMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/inverse_dispatch.py | 64 | `PromptMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/inverse_dispatch.py | 73 | `ArchitectureMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/inverse_dispatch.py | 82 | `CodeMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/inverse_dispatch.py | 91 | `PrincipleRemovalMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/inverse_dispatch.py | 100 | `BranchMutator` | 1 | 0 | 2 | KEEP | Plug-in `Mutator`. |
| meta/rollout/regression/statistical.py | 61 | `StatisticalSampleSource` | 1 | 0 | 3 | KEEP | Plug-in `Source`. |
| meta/rollout/roster.py | 21 | `OrgRoster` | 1 | 0 | 5 | KEEP | Plug-in `Roster`. |
| meta/signals/protocol.py | 26 | `SignalAggregator` | 1 | 0 | 4 | REVIEW | Duplicates the name in `meta/protocol.py:38`; one of the two should fold. |

### `src/synthorg/notifications/`, `observability/`, `ontology/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| observability/audit_chain/timestamping.py | 64 | `TimestampProvider` | 0 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| observability/tracing/protocol.py | 24 | `TraceHandler` | 0 | 0 | 0 | REMOVE | No usages. |
| ontology/drift/protocol.py | 11 | `DriftDetectionStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| ontology/injection/protocol.py | 17 | `OntologyInjectionStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| notifications/protocol.py | 10 | `NotificationSink` | 1 | 0 | 7 | KEEP | Plug-in `Sink`. |
| observability/audit_chain/protocol.py | 30 | `AuditChainSigner` | 1 | 0 | 8 | KEEP | Plug-in `Signer`. |

### `src/synthorg/persistence/`

All 45 persistence protocols are listed; every one is a plug-in `Repository` / `Backend` / `Store` / `Capability` per `docs/reference/persistence-boundary.md`, so they all classify as **KEEP**. No `REMOVE` candidates. The single anomaly is `IsDuplicate` (private internal seam, classified `REVIEW`).

| Path | Line | Name | rc | impl | testuse | Recommendation |
|---|---|---|---|---|---|---|
| persistence/_shared/audit.py | 160 | `IsDuplicate` | 0 | 0 | 0 | REVIEW |
| persistence/agent_state_protocol.py | 12 | `AgentStateRepository` | 1 | 0 | 3 | KEEP |
| persistence/approval_protocol.py | 28 | `ApprovalRepository` | 1 | 0 | 5 | KEEP |
| persistence/artifact_protocol.py | 11 | `ArtifactRepository` | 1 | 0 | 4 | KEEP |
| persistence/artifact_storage.py | 18 | `ArtifactStorageBackend` | 1 | 0 | 6 | KEEP |
| persistence/audit_protocol.py | 13 | `AuditRepository` | 1 | 0 | 6 | KEEP |
| persistence/auth_protocol.py | 22 | `SessionRepository` | 1 | 0 | 3 | KEEP |
| persistence/auth_protocol.py | 94 | `LockoutRepository` | 1 | 0 | 1 | KEEP |
| persistence/auth_protocol.py | 142 | `RefreshTokenRepository` | 1 | 0 | 4 | KEEP |
| persistence/checkpoint_protocol.py | 19 | `CheckpointRepository` | 1 | 0 | 7 | KEEP |
| persistence/checkpoint_protocol.py | 72 | `HeartbeatRepository` | 1 | 0 | 5 | KEEP |
| persistence/circuit_breaker_protocol.py | 33 | `CircuitBreakerStateRepository` | 0 | 0 | 1 | KEEP (promote `@runtime_checkable` candidate) |
| persistence/connection_protocol.py | 20 | `ConnectionRepository` | 1 | 0 | 7 | KEEP |
| persistence/connection_protocol.py | 69 | `ConnectionSecretRepository` | 1 | 0 | 0 | KEEP (review usage; testuse=0 unusual for persistence) |
| persistence/connection_protocol.py | 95 | `OAuthStateRepository` | 1 | 0 | 14 | KEEP |
| persistence/connection_protocol.py | 157 | `WebhookReceiptRepository` | 1 | 0 | 6 | KEEP |
| persistence/cost_record_protocol.py | 10 | `CostRecordRepository` | 1 | 0 | 5 | KEEP |
| persistence/custom_rule_protocol.py | 10 | `CustomRuleRepository` | 1 | 0 | 7 | KEEP |
| persistence/decision_protocol.py | 24 | `DecisionRepository` | 1 | 0 | 12 | KEEP |
| persistence/fine_tune_protocol.py | 18 | `FineTuneRunRepository` | 1 | 0 | 3 | KEEP |
| persistence/fine_tune_protocol.py | 60 | `FineTuneCheckpointRepository` | 1 | 0 | 2 | KEEP |
| persistence/idempotency_protocol.py | 156 | `IdempotencyRepository` | 1 | 0 | 6 | KEEP |
| persistence/jsonb_capability.py | 19 | `JsonbQueryCapability` | 1 | 0 | 18 | KEEP |
| persistence/mcp_protocol.py | 16 | `McpInstallationRepository` | 1 | 0 | 3 | KEEP |
| persistence/memory_protocol.py | 24 | `OrgFactRepository` | 1 | 0 | 1 | KEEP |
| persistence/message_protocol.py | 10 | `MessageRepository` | 1 | 0 | 5 | KEEP |
| persistence/ontology_protocol.py | 23 | `OntologyEntityRepository` | 1 | 0 | 2 | KEEP |
| persistence/ontology_protocol.py | 96 | `OntologyDriftReportRepository` | 1 | 0 | 2 | KEEP |
| persistence/parked_context_protocol.py | 10 | `ParkedContextRepository` | 1 | 0 | 6 | KEEP |
| persistence/preset_override_protocol.py | 24 | `PresetOverrideRepo` | 1 | 0 | 5 | KEEP |
| persistence/preset_protocol.py | 38 | `PersonalityPresetRepository` | 1 | 0 | 5 | KEEP |
| persistence/project_cost_aggregate_protocol.py | 28 | `ProjectCostAggregateRepository` | 1 | 0 | 4 | KEEP |
| persistence/project_protocol.py | 11 | `ProjectRepository` | 1 | 0 | 6 | KEEP |
| persistence/protocol.py | 134 | `PersistenceBackend` | 1 | 0 | 569 | KEEP |
| persistence/provider_audit_protocol.py | 31 | `ProviderAuditRepo` | 1 | 0 | 5 | KEEP |
| persistence/risk_override_protocol.py | 13 | `RiskOverrideRepository` | 1 | 0 | 1 | KEEP |
| persistence/secret_backends/protocol.py | 14 | `SecretBackend` | 1 | 0 | 0 | KEEP (pluggable backend; verify) |
| persistence/settings_protocol.py | 10 | `SettingsRepository` | 1 | 0 | 37 | KEEP |
| persistence/ssrf_violation_protocol.py | 13 | `SsrfViolationRepository` | 1 | 0 | 7 | KEEP |
| persistence/subworkflow_protocol.py | 30 | `SubworkflowRepository` | 1 | 0 | 4 | KEEP |
| persistence/task_protocol.py | 11 | `TaskRepository` | 1 | 0 | 15 | KEEP |
| persistence/training_protocol.py | 18 | `TrainingPlanRepository` | 1 | 0 | 8 | KEEP |
| persistence/training_protocol.py | 92 | `TrainingResultRepository` | 1 | 0 | 6 | KEEP |
| persistence/user_protocol.py | 15 | `UserRepository` | 1 | 0 | 23 | KEEP |
| persistence/user_protocol.py | 143 | `ApiKeyRepository` | 1 | 0 | 3 | KEEP |
| persistence/workflow_definition_protocol.py | 11 | `WorkflowDefinitionRepository` | 1 | 0 | 10 | KEEP |
| persistence/workflow_execution_protocol.py | 13 | `WorkflowExecutionRepository` | 1 | 0 | 6 | KEEP |

### `src/synthorg/providers/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| providers/management/_capabilities_mixin.py | 55 | `_ServiceProtocol` | 0 | 0 | 0 | REVIEW | Private mixin seam. |
| providers/management/local_models.py | 128 | `LocalModelManager` | 1 | 0 | 0 | REMOVE | No external usages. |
| providers/protocol.py | 21 | `CompletionProvider` | 1 | 0 | 87 | KEEP | Core pluggable subsystem. |
| providers/routing/selector.py | 30 | `ModelCandidateSelector` | 1 | 0 | 3 | KEEP | Plug-in `Selector`. |
| providers/routing/strategies.py | 56 | `RoutingStrategy` | 1 | 0 | 2 | KEEP | Plug-in `Strategy`. |

### `src/synthorg/security/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| security/autonomy/protocol.py | 10 | `AutonomyChangeStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| security/timeout/protocol.py | 40 | `RiskTierClassifier` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| security/trust/protocol.py | 16 | `TrustStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| security/output_scan_policy.py | 29 | `OutputScanResponsePolicy` | 1 | 0 | 6 | KEEP | Plug-in `Policy`. |
| security/policy_engine/protocol.py | 13 | `PolicyEngine` | 1 | 0 | 3 | KEEP | Plug-in `Engine`. |
| security/protocol.py | 18 | `SecurityInterceptionStrategy` | 1 | 0 | 2 | KEEP | Plug-in `Strategy`. |
| security/risk_scorer.py | 129 | `RiskScorer` | 1 | 0 | 2 | KEEP | Plug-in `Scorer`. |
| security/rules/protocol.py | 10 | `SecurityRule` | 1 | 0 | 3 | KEEP | Plug-in `Rule`. |
| security/timeout/protocol.py | 15 | `TimeoutPolicy` | 1 | 0 | 1 | KEEP | Plug-in `Policy`. |

### `src/synthorg/settings/`, `telemetry/`, `templates/`, `tools/`

| Path | Line | Name | rc | impl | testuse | Recommendation | Notes |
|---|---|---|---|---|---|---|---|
| templates/_inheritance.py | 24 | `_RenderToDictFn` | 0 | 0 | 0 | REVIEW | Private callable seam. |
| templates/model_matcher.py | 370 | `_ModelMatcherBridge` | 0 | 0 | 0 | REVIEW | Private bridge seam. |
| tools/sandbox/lifecycle/protocol.py | 36 | `SandboxLifecycleStrategy` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| tools/web/web_search.py | 45 | `WebSearchProvider` | 1 | 0 | 0 | REMOVE | Plug-in suffix but no consumer. |
| settings/subscriber.py | 11 | `SettingsSubscriber` | 1 | 0 | 27 | KEEP | Public extension surface. |
| telemetry/event_counter_protocol.py | 30 | `TelemetrySubscriber` | 1 | 0 | 2 | KEEP | Plug-in `Subscriber`. |
| telemetry/event_counter_protocol.py | 49 | `TelemetryEventCounter` | 1 | 0 | 2 | KEEP | Plug-in `Counter`. |
| telemetry/protocol.py | 68 | `TelemetryReporter` | 1 | 0 | 5 | KEEP | Plug-in `Reporter`. |
| tools/analytics/data_aggregator.py | 37 | `AnalyticsProvider` | 1 | 0 | 8 | KEEP | Plug-in `Provider`. |
| tools/analytics/metric_collector.py | 28 | `MetricSink` | 1 | 0 | 7 | KEEP | Plug-in `Sink`. |
| tools/communication/notification_sender.py | 36 | `NotificationDispatcherProtocol` | 1 | 0 | 2 | KEEP | Plug-in `Dispatcher`. |
| tools/design/image_generator.py | 53 | `ImageProvider` | 1 | 0 | 5 | KEEP | Plug-in `Provider`. |
| tools/discovery.py | 44 | `ToolDisclosureManager` | 1 | 0 | 4 | KEEP | Plug-in `Manager`. |
| tools/protocol.py | 25 | `ToolInvokerProtocol` | 1 | 0 | 7 | KEEP | Public extension surface. |
| tools/sandbox/protocol.py | 17 | `SandboxBackend` | 1 | 0 | 41 | KEEP | Pluggable subsystem. |

## Post-cleanup status (2026-05-11)

Issue [#1864](https://github.com/Aureliolo/synthorg/issues/1864) hand-reviewed every REMOVE row below. The verification protocol was: (1) re-grep the protocol name across `src/synthorg/` and `tests/`, (2) inspect sibling files in the same directory for structural impls, (3) confirm whether a factory dispatch dict / config discriminator / multi-slot injection / vendor-agnostic design exists. The script's `impl=0` reliably under-counts these patterns.

### Deleted (2 protocols)

The script's classification matched reality: zero structural impls, zero consumers (or single-file Callable seam).

| Path | Line | Name | Outcome |
|---|---|---|---|
| api/auto_wire.py | 93 | `BuildDispatcherFn` | Deleted. Collapsed to `Callable[..., SettingsChangeDispatcher \| None]` at the single annotation site (`auto_wire_settings`). |
| communication/event_stream/consumer.py | 15 | `EventStreamConsumer` | Deleted. File removed (zero refs anywhere in repo). |

### Audit re-flagged to KEEP (44 protocols)

Each retained protocol carries a one-line `# <reason>` design-rationale comment immediately above the `class` line describing the implementations, factory dispatch, and consumer wiring that justify keeping it. The categories below explain the structural patterns the audit script's regex missed; the in-code comments name the concrete impls/factories/consumers so future readers can verify the wiring without re-running the audit.

Categories of re-flag rationale:

1. **Multiple structural impls in the same file or sibling file** (impl count missed by the regex). Examples: `ConfidenceFormatter` (4 impls + `_FORMATTERS` factory in same file), `ImpactScorer` (3 impls), `StrategicContextProvider` (2 impls), `CostTierResolver` (2 impls + factory), `ShadowTaskProvider` (2 impls in `shadow_providers.py`), `TimestampProvider` (2 impls in same file), `TraceHandler` (`NoopTraceHandler` in same file + `OtlpTraceHandler` in sibling), `TrustStrategy` (4 strategy files + 117 test occurrences), 9 of 10 hr/ candidates (impl files named `*_strategy.py`), `LocalModelManager` (`OllamaModelManager` in same file). <!-- lint-allow: doc-numeric-macros -- structural-impl audit occurrence counts, not build-time stats -->

2. **Factory or registry dispatch** that the script's regex cannot see. Examples: `ConnectionAuthenticator` (registry in `connections/types/__init__.py`), `ConnectionHealthCheck` (`_CHECK_REGISTRY` in `prober.py`), `CaptureStrategy`/`PropagationStrategy`/`PruningStrategy` (each backed by a per-area `factory.py` with 3 impls), `OntologyInjectionStrategy` (`injection/factory.py`), `RiskTierClassifier` (`timeout/factory.py` + `engine/_security_factory`), `SandboxLifecycleStrategy` (`create_lifecycle_strategy` config-discriminated factory with 3 impls).

3. **Default-impl injection with safe fallback**. Examples: `MemoryArchivalStrategy` (`FullSnapshotStrategy` default + `OffboardingService` injection), `TaskReassignmentStrategy` (`QueueReturnStrategy` default), `AutonomyChangeStrategy` (`HumanOnlyPromotionStrategy`), `ReviewStage` (`ClientReviewStage` + `InternalReviewStage` walked by `ReviewPipeline`), `OutcomeStore`, `ConfidenceAdjuster`, `CIValidator`, `AnalyticsEmitter`/`Collector`/`RecommendationProvider` (full telemetry trio with `meta/telemetry/factory.py`), `DriftDetectionStrategy` (`LayeredDriftDetector`), `StepQualityClassifier` (`RuleBasedStepClassifier`).

4. **Vendor-agnostic public extension surface** (no built-in impl by design; consumed via injection from MCP / user / external integration). Examples: `JudgeEvaluator` (debate/hybrid LLM judge), `WebSearchProvider` (threaded through `tools/factory.py` at 4 callsites), `ShadowAgentRunner` (production wires to `AgentEngine.run` via caller-supplied adapter), `TunnelProvider` (ngrok/cloudflared abstraction wired via `IntegrationsBundle`), `OrgInflectionSink` and `AlertSink` (downstream observability hooks).

5. **Multi-impl injection points missed by the audit doc's own duplicate detection**. `ParticipantResolver`@`meeting/participant.py:56` was flagged as a "dead duplicate" of an alleged twin in `meeting/protocol.py`; the twin does not exist. The participant.py copy has 2 impls (`PassthroughParticipantResolver`, `RegistryParticipantResolver`) in the same file and 3 test files. <!-- lint-allow: doc-numeric-macros -- structural-impl audit occurrence counts, not build-time stats -->

### Issue #1865 outcomes (REVIEW pass + duplicate folds)

Issue [#1865](https://github.com/Aureliolo/synthorg/issues/1865) closed out the 11 REVIEW rows, which cover 12 protocol names: 9 `_PrivatePrefixed` typing seams (the `engine/coordination/dispatcher_types.py` row lists 2 names in one snapshot entry), the non-prefixed `IsDuplicate` predicate in `persistence/_shared/audit.py`, and the 2 duplicate-named-pair REVIEW rows. The 5 retained protocols each carry a one-line `# <reason>` rationale comment naming the consumer that justifies the seam; the 7 deletions (5 single-consumer seam collapses plus 2 duplicate-pair folds) collapsed single-consumer seams or removed pure dead code.

#### Retained with rationale (5 protocols)

Each gets a one-line `# <reason>` design-rationale comment immediately above the `class` line.

| Path | Line | Name | Rationale |
|---|---|---|---|
| api/lifecycle.py | 86 | `_AsyncStartStop` | Structural seam over the optional `synthorg[distributed]` `JetStreamTaskQueue`; consumed by `_cleanup_on_failure` and `_safe_shutdown`. |
| core/state_machine.py | 36 | `_HasValue` | Generic bound for `StateMachine[S]`; 4 structural users: `TaskStatus`, `RequestStatus`, `KanbanColumn`, `SprintStatus`. |
| persistence/_shared/audit.py | 160 | `IsDuplicate` | Driver-abstraction predicate; impls: `sqlite._shared.is_unique_constraint_error`, `postgres.audit_repository._postgres_is_duplicate`. |
| templates/_inheritance.py | 24 | `_RenderToDictFn` | Self-referential recursive callback for `render_parent_config`; consumer: `templates.renderer._render_to_dict` passes itself in to walk the parent chain. |
| providers/management/_capabilities_mixin.py | 55 | `_ServiceProtocol` | Narrows the mixin's `self`-type to the 3 attrs + 3 methods consumed; host: `ProviderManagementService`. |

#### Deleted (7 protocols)

| Path | Line | Name | Outcome |
|---|---|---|---|
| engine/quality/graders/heuristic.py | 27 | `_HeuristicGraderBridge` | Deleted. Collapsed to `bridge: EngineBridgeConfig` with a `TYPE_CHECKING` import; the single structural consumer was already `EngineBridgeConfig`. |
| engine/routing/scorer.py | 40 | `_RoutingScorerBridge` | Deleted. Same collapse as `_HeuristicGraderBridge`. |
| templates/model_matcher.py | 370 | `_ModelMatcherBridge` | Deleted. Same collapse as `_HeuristicGraderBridge`. |
| engine/coordination/attribution.py | 32 | `_ExecutionResultLike` | Deleted. Defined inside `if TYPE_CHECKING:` but never referenced as an annotation; runtime code already used `getattr()` on untyped values. Duck-type contract documented inline at the call site. |
| engine/coordination/attribution.py | 35 | `_AgentRunResultLike` | Deleted. Same as `_ExecutionResultLike`. |
| communication/meeting/conflict_detection.py | 39 | `ConflictDetector` (fold duplicate) | Deleted. Folded into the canonical definition at `communication/meeting/protocol.py:41`. The 5 importers already pointed at `protocol.py`; the `conflict_detection.py` copy was unimported dead code. |
| meta/signals/protocol.py | 26 | `SignalAggregator` (fold duplicate) | Deleted. File removed entirely (zero importers; sole content was this dead protocol). Folded into the canonical definition at `meta/protocol.py:38`. |

### Recommended next pass for `scripts/protocol_audit.py`

A follow-up issue should teach the audit script to (a) consult a manual override file (e.g. `data/protocols_audit_overrides.yaml`) keyed on `path:line:name` so KEEP decisions made here survive future regenerations, and (b) detect structural impls heuristically (same-file `class X:` siblings whose method names match the Protocol's, factory return type annotations) to reduce false-positive REMOVE flags.

## Follow-up issues

The `REMOVE` and `REVIEW` candidates above are filed as separate cleanup issues against this audit doc. Each issue must re-verify usage at cleanup time; a `REMOVE` flag here is not pre-approval to delete. Per-area issue titles:

- `[CLEANUP] Protocols audit follow-up: REMOVE pass for hr/`
- `[CLEANUP] Protocols audit follow-up: REMOVE pass for meta/chief_of_staff/ + meta/telemetry/`
- `[CLEANUP] Protocols audit follow-up: REMOVE pass for engine/strategy/ + engine/quality/ + engine/review/`
- `[CLEANUP] Protocols audit follow-up: REMOVE pass for memory/procedural/`
- `[CLEANUP] Protocols audit follow-up: REMOVE pass for security/ + integrations/connections/health/tunnel/ + ontology/ + tools/sandbox/web_search`
- `[CLEANUP] Protocols audit follow-up: REVIEW pass for _PrivatePrefixed typing seams`
- `[CLEANUP] Protocols audit follow-up: fold duplicate-named protocols (ConflictDetector, SignalAggregator)`
- `[CLEANUP] Protocols audit follow-up: meta-tracking issue umbrella`

Each issue body links back to the specific rows in this document and requires the owner to (a) re-grep, (b) confirm no future-second-implementation plan, (c) collapse the consumer onto a concrete type or fold the duplicate.

## Post-#1891 status (RFC: pluggable subsystem cleanup)

Issue #1891 ran a per-candidate keep / inline / refactor decision against the eight subsystems flagged as adding indirection without polymorphism, or sitting on the wrong dimension. Each decision is reflected in code; the eight commits live on `refactor/pluggable-subsystem-cleanup`.

| Candidate | Decision | Outcome |
|---|---|---|
| 1. `InjectionStrategy.SELF_EDITING` enum | Refactor: wire the factory | `memory/injection_factory.py` dispatches all three strategies; conformance test asserts the runtime-checkable Protocol contract. |
| 2. `RecoveryStrategy` + `CheckpointRecoveryStrategy` | Keep + wire CheckpointRecovery | `engine/recovery_factory.py` dispatches `FAIL_REASSIGN` and `CHECKPOINT` via `EngineRecoveryConfig`; `RecoveryConfigError(EngineError)` surfaces misconfiguration at boot. |
| 3. `ConflictDetector` + 6 impls | Keep + add factory dispatch | `communication/meeting/factory.py::build_conflict_detector` dispatches all six structural impls keyed on `ConflictDetectorType`. |
| 4. `OrgMemoryBackend` factory | Inline | `memory/org/factory.py` deleted; `OrgMemoryConfig.backend` discriminator removed; HR / knowledge-architect callers continue to depend on the protocol type. |
| 5. `HumanDecisionProcessor.mode` | Split | `WinnerOnlyDecisionProcessor` + `HybridDecisionProcessor` replace the `mode` discriminator; new `EscalationDecisionShapeError(ConflictResolutionError)` satisfies the DomainError contract. |
| 6. `TrustService` disabled strategy | Conditional instantiation | `_build_default_trust_service` and `DisabledTrustStrategy` deleted; `security/trust/factory.py::build_trust_strategy` returns `None` for `TrustStrategyType.DISABLED` so the caller skips `TrustService` construction entirely. |
| 7. `ontology/versioning.py` factories | Invert dependency | Backend-specific factories moved to `persistence/sqlite/ontology_versioning.py` and `persistence/postgres/ontology_versioning.py`; `ontology/versioning.py` keeps only the pure `EntityDefinition` deserializer helpers. Dual-backend conformance test in `tests/conformance/persistence/test_ontology_versioning.py`. |
| 8. `backup/factory.py` | Backend-pluggable | `backup/registry.py::PERSISTENCE_BACKUP_HANDLER_REGISTRY` dispatches by persistence backend; SQLite handler renamed to `SQLitePersistenceComponentHandler`; new `PostgresPersistenceComponentHandler` shells out to `pg_dump` / `pg_restore` with `PGPASSWORD` env-injection; dual-backend conformance test in `tests/conformance/persistence/test_backup_round_trip.py`. |

## Collaborator protocols added by #2315

Issue #2315 added six `@runtime_checkable` structural Protocols that decouple
high-fan-in consumers from concrete collaborators (so the real class and the
test doubles both satisfy a declared contract). They are consumer-decoupling
seams, KEEP by design intent, and each is covered by
`tests/unit/_core/test_protocol_conformance.py`. They postdate the
2026-05-10 snapshot above and are recorded here rather than in the historical
per-area tables.

| Path | Name | rc | Recommendation | Notes |
|---|---|---|---|---|
| workers/queue_protocol.py | `TaskQueue` | 1 | KEEP | Structural seam over the optional `synthorg[distributed]` `JetStreamTaskQueue`. |
| engine/parallel_protocol.py | `ParallelExecutorProtocol` | 1 | KEEP | `execute_group` seam decoupling dispatchers from the heavy `AgentEngine`. |
| settings/resolver_protocol.py | `ConfigResolverProtocol` | 1 | KEEP | Scalar + bridge-config read surface of `ConfigResolver`. |
| settings/service_protocol.py | `SettingsServiceProtocol` | 1 | KEEP | Setting-value lifecycle surface of `SettingsService`. |
| budget/tracker_protocol.py | `CostTrackerProtocol` | 1 | KEEP | Record/aggregate surface of `CostTracker`. |
| hr/registry_protocol.py | `AgentRegistryProtocol` | 1 | KEEP | Lookup + identity-lifecycle surface of `AgentRegistryService`. |

## Collaborator protocols added by #2316

The api forward-ref hoist added three `@runtime_checkable` structural handles in
`workers/distributed_protocols.py`. They give the construction-phase
`Phase1Result` (in `api/auto_wire_phase1.py`) and the `RuntimeStateSlice` (in
`workers/state.py`) a runtime-resolvable annotation for the optional
`synthorg[distributed]` concrete types without importing them (those sit in the
`workers.config` -> `communication.config` cold-import cycle). The real objects
satisfy them structurally; KEEP by design intent.

| Path | Name | rc | Recommendation | Notes |
|---|---|---|---|---|
| workers/distributed_protocols.py | `DistributedTaskQueueHandle` | 1 | KEEP | `is_running` / `start` / `stop` seam over `JetStreamTaskQueue`. |
| workers/distributed_protocols.py | `DistributedDispatcherHandle` | 1 | KEEP | `on_task_state_changed` observer + `set_workers_bridge_provider` late-bind seam over `DistributedDispatcher`. |
| workers/distributed_protocols.py | `DistributedBackendServicesHandle` | 1 | KEEP | `start` / `stop` seam over `DistributedBackendServices`. |

## Out of scope

- Actually deleting any `Protocol` class (cleanup PRs).
- Adding `@runtime_checkable` decoration anywhere (separate pass; needs `isinstance(_, <Name>)` site survey).
- Auditing test-only `Protocol` definitions (`tests/`).
- Auditing third-party / vendored protocols.
