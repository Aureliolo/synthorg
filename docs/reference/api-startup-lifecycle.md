---
title: API Startup Lifecycle
description: Construction-phase vs on-startup wiring order, the gated best-effort wiring hooks, and the ordering invariants that keep boot from racing or poisoning startup.
---

# API Startup Lifecycle

The API boots in two phases. **Construction** (the `create_app` body) wires synchronous services. **On-startup** (`_build_lifecycle.on_startup`) wires services that need a connected persistence backend. The ordering invariants below are load-bearing: getting them wrong races a dependency and 503s a controller forever, or poisons startup on a transient boot.

## Construction-phase ordering invariants

- `agent_registry` must be built BEFORE `auto_wire_meetings`.
- `tunnel_provider` is wired unconditionally (not gated by `integrations.enabled`).

## On-startup ordering invariants

- `SettingsService` auto-wire must precede `WorkflowExecutionObserver` registration, so it picks up the resolver-driven `max_subworkflow_depth` instead of the seed default.
- `OntologyService` wires after `persistence.connect()` via `_wire_ontology_service`.
- Cost-dial services (`BudgetConfig`, `CostForecastRepository`, `BenchmarkScoreRepository`, the `budget.benchmark_provider`-selected `BenchmarkScoreProvider`, `CostForecaster`, `ParetoAnalyzer`) wire via `_try_wire_cost_dial` AFTER persistence connects. It is best-effort (logs an `API_APP_STARTUP` warning and the controllers 503 if it fails or persistence is absent) and idempotent (skips when already wired), so a transient shared-app boot does not poison startup. The benchmark provider, repo, and `ParetoAnalyzer`'s `ModelTierMap` are built in `api/_benchmark_wiring.py` (`stub` default / `measured`; `seed_benchmark_scores` boot-seeds the measured arm from the committed `benchmark_seed.json`). The approved forecast's `forecast_id` + `ceiling_amount` are stamped onto the `Task` in the work pipeline's intake phase (`WorkPipelineService._link_forecast`) so the in-loop `BudgetChecker` enforces the per-brief ceiling and the engine can stamp halt context for the resume banner.
- Knowledge substrate wires via `_wire_knowledge_engine` AFTER persistence connects; best-effort and gated on `has_persistence` AND `has_memory_backend` (logs an `API_APP_STARTUP` warning and the knowledge controllers + MCP handlers 503 if either is absent), so a missing memory backend in dev does not poison startup.
- `EnvironmentService` (per-project reproducible environments) wires in `_install_runtime_services` behind `has_persistence` and is threaded into `AgentEngineExecutionService` via `build_runtime_services`; the worker provisions ambiently (`ActiveSandboxEnvironment` contextvar) before the engine run, so a missing workspace logs `ENVIRONMENT_PROVISION_SKIPPED` rather than silently dropping the declared env.
- Mid-flight steering splits its wiring in two by dependency: the steering INBOX (read path) is built from `persistence.project_brain` and injected into the boot `AgentEngine` in the runtime-services step (persistence-only, memory-independent `list_current` projection), while the steering SERVICE (write path) wires in `_wire_steering_service` AFTER `_wire_project_brain` (memory-gated brain) via partial `app_state.wire(CockpitStateSlice, ...)` (NOT `swap_slice`, so the construction-phase `steering_notifier` and the later `steering_service` coexist on the slice). Wiring the service inside `_wire_cockpit_services` would race the brain and 503 forever.
- The red-team report repo is published on `SecurityStateSlice.red_team_reports` during `_install_runtime_services` (decoupled from the review gate, via partial `app_state.wire`), and `_wire_deliverable_receipts` reads it so a receipt's `red_team` section degrades to empty rather than erroring when the subsystem is off.

## Late-startup gated wiring hooks

- `wire_quota_poller`: gated `budget.quota_poller_enabled` + connected persistence + wired `quota_tracker`; appended AFTER `_wire_features`; idempotent; stopped in `lifecycle_runner_shutdown`.
- `wire_risk_override_service`: gated on a `TieredTimeoutConfig` + persistence + scheduler; rebuilds the tiered policy's classifier wrapped in `SecOpsRiskClassifier` seeded from the durable override repo and swaps it via `ApprovalTimeoutScheduler.set_timeout_policy`; publishes `RiskOverrideService` on `SecurityStateSlice`; controllers + MCP 503 when unwired.
- `wire_org_memory_backend`: inside `wire_features_on_startup` BEFORE `_wire_pruning`; persistence-only gate; best-effort `connect()`; publishes `MemoryStateSlice.org_memory_backend` consumed by offboarding + ontology sync, degrading to `None` when absent.
- `HealthMonitoringPipeline` (judge + triage + dispatcher) is built in `build_runtime_services` and threaded into `AgentEngineExecutionService` (`health_pipeline=` / `health_enabled=`), re-reading `engine.health_monitoring_enabled` per run; absent dispatcher means no pipeline.

## Controller-facing read services

- `ConversationalResumeService` lives in `meta/chief_of_staff/` (NOT `api/services/`, so the early `api_core_state` import chain never pulls the `communication` enum modules and trips the cold-import cycle gate), is published on `MetaStateSlice` by `_wire_conversational_repositories_and_reconcile`, and is deliberately UNGATED (wraps only the persistence repos, never the toggle-gated Chief-of-Staff feature services) so a decided conversational approval still resolves after its feature switches off.
- The single `WorkflowExecutionService` (`_wire_workflow_observer`) caches the `config_resolver` and resolves `max_subworkflow_depth` per call so settings hot-reload is preserved (constructing with a resolved int would freeze the seed default; a `None` resolver logs a warning and falls back to `EngineBridgeConfig().max_subworkflow_depth`).
- `_wire_webhook_request_services` wires `webhook_replay_protector` unconditionally and `webhook_activity_service` only on connected persistence. Idempotency keys on mutating endpoints MUST bind the resource id (`f"{resource_id}:{idempotency_key}"`) so a reused key cannot collide across resources.

## Self-extending toolkit (toolsmith)

Autonomous detection wires in `wire_toolsmith` AFTER `install_dynamic_tool_layer` (gated on `tool_creation_enabled` + provider + connected persistence): `install_capability_gap_sink(runtime.service)` routes every `capability_gap` MCP envelope into the gap store (fire-and-forget; write failure logs via `safe_error_description`, never an unhandled task-exception traceback), and a `ToolsmithCycleScheduler` (cadence `toolsmith.cycle_interval_seconds`, floor 60s) drives `run_cycle` periodically with a `meta.toolsmith_cycle_paused` kill-switch (re-read each tick, fail-safe to enabled). The `ToolsmithStateSlice` carries `service` + `cycle_scheduler` as a both-or-neither paired invariant; `lifecycle_runner_shutdown` stops the scheduler. The cycle only PROPOSES (human approval still gates apply).

## Periodic model-refresh

Wires in `wire_model_refresh` (`_wire_model_refresh`, AFTER `wire_toolsmith`), gated on `providers.model_refresh_mode != off` AND a built provider-management service AND connected persistence (off-by-default, so a normal boot skips it). The cadence modes (`detect_only` / `reconcile_recommend`) build + `start()` a `ModelRefreshScheduler` BEFORE publishing `ModelRefreshStateSlice` and roll back (no publish) if start fails; `manual_only` wires the on-demand service with no scheduler. The slice's both-or-neither invariants: a `scheduler` implies a `service`, and a `service` implies a `recommendation_repo`. `lifecycle_runner_shutdown` stops the scheduler. The scheduler re-reads the live mode + auto-apply flag each tick (fail-safe to `off`), and `stop()` does NOT reset its lifecycle lock/event to None (it passes `reset_primitives_on_stop=False` to the shared `AsyncCycleScheduler` base: clearing them under the held lock opens a rebind race).

## Runtime tool-call failure feedback

Wires in `wire_tool_call_feedback` (AFTER `_wire_model_refresh`), gated on a built provider-management service AND connected persistence (NOT on `providers.tool_call_feedback_enabled`: the `ToolCallFeedbackTracker` re-reads that setting live per observation so the feature toggles without a restart while the cheap sink stays installed). Best-effort + idempotent: a missing resolver / management / persistence logs an `API_APP_STARTUP` warning and returns (never an invisible skip). The tracker is published on `ToolCallFeedbackStateSlice`, then `install_tool_call_signal_sink(tracker)` runs LAST so a partial build leaves no dangling sink; `lifecycle_runner_shutdown` uninstalls the sink + clears the slice. The tracker keeps its instance lock OFF the capability-writer call (the writer takes `ProviderManagementService._lock`), so the two locks never nest. The matcher hard-fails `requires_tools` agents on `tool_calls_verified is False` (authoritative over the optimistic `supports_tools` path); a genuine tool-call success only re-enables a truly-downgraded (`False`) model, never promotes an untested (`None`) one.

## Stakes-gated red-team completion

`_wire_red_team_completion` threads `stakes_routing.red_team_min_stakes` (default HIGH) onto `ReviewGateService` via `set_red_team_min_stakes`, so the gate fires only for `task.stakes >= red_team_min_stakes`; a below-threshold completion logs `RED_TEAM_GATE_SKIPPED` and `dispatch_completion` runs it INLINE (a missing task also runs inline so `complete_review` raises `TaskNotFoundError` synchronously) rather than deferring a no-op to the background.

## Self-improvement rollback executor

Wires in `api/lifecycle_helpers/meta_apply_wiring.py::_wire` (best-effort + idempotent, off by default with the self-improvement feature): `_build_rollback_executor` constructs the six mutators (config/prompt/principle-removal/architecture/code unconditional, branch gated) and threads the executor into `SelfImprovementService`, which dispatches the applier-MATERIALISED inverse operations (carried on `RolloutResult.applied_rollback_operations`, NOT the proposal's static plan) on a post-rollout regression, flipping `REGRESSED` to `ROLLED_BACK`. The `branch`/`revert_branch` handler is gated on `code_modification.github_token` + `github_repo` (absent means branch revert omitted, matching the applier's credential gating); `github_api_url` is `https`-validated (token rides the `Authorization` header). A materialised op with no registered handler logs ERROR `reason="unregistered_operation_type"` and keeps `REGRESSED` (never a silent swallow).

## Runtime services selection

`synthorg.workers.runtime_builder.build_runtime_services` selects behind ONE provider-present switch and returns a `RuntimeServices` pair (worker execution service + multi-agent coordinator) built from a SINGLE shared boot `AgentEngine`: `AgentEngineExecutionService` + a `build_coordinator(...)` coordinator with a provider, or `NoProviderExecutionService` + `None` coordinator as the empty-company backstop. The provider-present arm validates `coordination.decomposition_model` is non-blank before building the coordinator and raises `CoordinationConfigError` at boot when it is empty (the setting ships blank, so an operator must set a model id from their catalogue). The `_install_runtime_services` boot hook installs both via the `AppState.worker_execution_service` and `AppState.coordinator` seams; it is appended FIRST after the persistence/SettingsService hooks so the once-only `set_worker_execution_service` and if-absent `set_coordinator_if_absent` seams cannot lose the race with the worker property's lazy `LifecycleAdvancingExecutionService` default. Empty-company rejects task creation at the controller (`AgentRuntimeNotConfiguredError`, 4014) and `/coordinate` honestly 503s (no coordinator). `swap_worker_execution_service` / `swap_coordinator` / `swap_provider_registry` hold a lock (synchronised against lazy reads).

## Setup completion

`post_setup_reinit()` (provider reload, agent bootstrap, AND runtime-services rebuild + dual hot-swap of the worker execution service and coordinator, defined in `src/synthorg/api/controllers/setup/_runtime_wiring.py`) propagates failures, and `settings_svc.set("api", "setup_complete", "true")` only runs if reinit returns clean. The whole check/validate/reinit/persist sequence is serialised under `COMPLETE_LOCK` (also in `_runtime_wiring.py`) so two concurrent `/setup/complete` requests cannot race on the flag write. A half-configured runtime presenting itself as "complete" is worse than a clear error the operator can retry after fixing the underlying provider config.
