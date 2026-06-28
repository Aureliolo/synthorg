---
title: Protocols Audit Cleanup Log
description: Dated per-issue cleanup history for the protocol audit, covering deletions, re-flagged keeps, and collaborator protocols added by later issues.
---

# Protocols Audit Cleanup Log

This is the dated cleanup history behind the evergreen [Protocols Audit](protocols-audit.md) classification. It records which protocols were deleted, re-flagged to KEEP, or folded by each cleanup pass, plus the collaborator protocols added by later issues. The audit reference itself carries the current per-area classification; this log is the per-issue trail.

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

Issue #2315 added six `@runtime_checkable` structural Protocols that decouple high-fan-in consumers from concrete collaborators (so the real class and the test doubles both satisfy a declared contract). They are consumer-decoupling seams, KEEP by design intent, and each is covered by `tests/unit/_core/test_protocol_conformance.py`. They postdate the 2026-05-10 snapshot and are recorded here rather than in the per-area tables.

| Path | Name | rc | Recommendation | Notes |
|---|---|---|---|---|
| workers/queue_protocol.py | `TaskQueue` | 1 | KEEP | Structural seam over the optional `synthorg[distributed]` `JetStreamTaskQueue`. |
| engine/parallel_protocol.py | `ParallelExecutorProtocol` | 1 | KEEP | `execute_group` seam decoupling dispatchers from the heavy `AgentEngine`. |
| settings/resolver_protocol.py | `ConfigResolverProtocol` | 1 | KEEP | Scalar + bridge-config read surface of `ConfigResolver`. |
| settings/service_protocol.py | `SettingsServiceProtocol` | 1 | KEEP | Setting-value lifecycle surface of `SettingsService`. |
| budget/tracker_protocol.py | `CostTrackerProtocol` | 1 | KEEP | Record/aggregate surface of `CostTracker`. |
| hr/registry_protocol.py | `AgentRegistryProtocol` | 1 | KEEP | Lookup + identity-lifecycle surface of `AgentRegistryService`. |

## Collaborator protocols added by #2316

The api forward-ref hoist added three `@runtime_checkable` structural handles in `workers/distributed_protocols.py`. They give the construction-phase `Phase1Result` (in `api/auto_wire_phase1.py`) and the `RuntimeStateSlice` (in `workers/state.py`) a runtime-resolvable annotation for the optional `synthorg[distributed]` concrete types without importing them (those sit in the `workers.config` -> `communication.config` cold-import cycle). The real objects satisfy them structurally; KEEP by design intent.

| Path | Name | rc | Recommendation | Notes |
|---|---|---|---|---|
| workers/distributed_protocols.py | `DistributedTaskQueueHandle` | 1 | KEEP | `is_running` / `start` / `stop` seam over `JetStreamTaskQueue`. |
| workers/distributed_protocols.py | `DistributedDispatcherHandle` | 1 | KEEP | `on_task_state_changed` observer + `set_workers_bridge_provider` late-bind seam over `DistributedDispatcher`. |
| workers/distributed_protocols.py | `DistributedBackendServicesHandle` | 1 | KEEP | `start` / `stop` seam over `DistributedBackendServices`. |

## Audit-table reconciliation by #2503

Issue #2503 was scoped (via #2478) to "collapse 9 single-implementation hr/ protocols + `LocalModelManager`", reading the stale `REMOVE` rows in [protocols-audit.md](protocols-audit.md). Those ten rows had already been hand-reviewed and re-flagged to KEEP by #1864 (see the "Audit re-flagged to KEEP" section above): the nine hr/ strategy protocols are default-impl injection seams (e.g. `OffboardingService` injects `FullSnapshotStrategy`/`QueueReturnStrategy`; the promotion / performance / evaluation services inject their `*Strategy` impls), `PruningPolicy` has two impls, and `LocalModelManager` is the vendor-agnostic local-model surface with `OllamaModelManager` in the same file. Each carries an in-code `# <reason>` rationale comment.

No protocol was deleted. The only defect was the audit.md table never being updated after #1864, so #2503 flipped those ten `REMOVE` rows to `KEEP` to match this log. The `LocalModelManager.pull_model` "sync-vs-async mismatch" #2503 also listed is a non-issue: a `def … -> AsyncIterator` member implemented by an `async def … yield` async generator type-checks correctly (calling an async generator returns an `AsyncIterator` without `await`); the in-code comment documents the intentional `def`-not-`async def` declaration.

## Out of scope

- Actually deleting any `Protocol` class (cleanup PRs).
- Adding `@runtime_checkable` decoration anywhere (separate pass; needs `isinstance(_, <Name>)` site survey).
- Auditing test-only `Protocol` definitions (`tests/`).
- Auditing third-party / vendored protocols.
