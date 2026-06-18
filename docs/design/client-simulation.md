---
title: Client Simulation
description: Synthetic client framework for generating task requirements, reviewing deliverables, and evaluating agent organisation performance.
---

# Client Simulation

The client simulation subsystem generates synthetic workloads that exercise the full
task lifecycle end-to-end. Simulated clients (AI-driven, human, or hybrid) submit task
requirements through an intake pipeline and review completed deliverables via a
configurable review pipeline. This enables systematic evaluation of agent performance,
organisational throughput, and quality metrics without real external clients.

---

## Architecture Overview

```d2
direction: right

clients: "ClientPool" {
  ai: "AIClient"
  human: "HumanClient"
  hybrid: "HybridClient"
}
reqgen: "RequirementGenerator" {
  template: "TemplateGenerator"
  llm: "LLMGenerator"
  dataset: "DatasetGenerator"
  hybrid: "HybridGenerator"
  procedural: "ProceduralGenerator"
}
feedback: "FeedbackStrategy" {
  binary: "BinaryFeedback"
  scored: "ScoredFeedback"
  criteria: "CriteriaCheckFeedback"
  adversarial: "AdversarialFeedback"
}
intake: "IntakeEngine" {
  strategy: "IntakeStrategy\n(direct / agent)"
}
engine: "TaskEngine" {
  created: "CREATED"
  assigned: "ASSIGNED"
  inprogress: "IN_PROGRESS"
  created -> assigned -> inprogress
}
review: "ReviewPipeline" {
  internal: "InternalReviewStage"
  client: "ClientReviewStage"
}
completed: "COMPLETED"
runner: "SimulationRunner"
report: "ReportStrategy" {
  detailed: "DetailedReport"
  summary: "SummaryReport"
  metrics: "MetricsOnly"
  json: "JsonExport"
}

reqgen -> clients: "generates requirements"
clients -> intake: "submit"
intake -> engine
feedback -> review: "scores"
engine -> review
review -> completed
completed -> runner
runner -> report
```

---

## Client Types

### ClientInterface Protocol

All client types implement `ClientInterface`, providing two operations:

- **`submit_requirement(context)`**: generate or submit a task requirement.
  Returns `None` when the client declines to participate.
- **`review_deliverable(context)`**: review a completed deliverable and return
  feedback with acceptance decision and reasoning.

### AIClient

LLM-backed client with a configurable persona. Uses `CompletionProvider` for
requirement generation and deliverable review. Persona-driven prompts based on
`ClientProfile` (expertise domains, strictness level).

### HumanClient

Delegates to the API/dashboard for human input. Uses an async callback pattern
for approval flows. No LLM calls; pure API/UI delegation.

### HybridClient

Composes `AIClient` + `HumanClient`: AI drafts requirements and evaluates
deliverables, human confirms or overrides decisions.

---

## Client Profile

```python
class ClientProfile(BaseModel):
    client_id: NotBlankStr
    name: NotBlankStr
    persona: NotBlankStr
    expertise_domains: tuple[NotBlankStr, ...]
    strictness_level: float  # 0.0 (lenient) to 1.0 (strict)
```

Profiles control how clients generate requirements and evaluate deliverables.
`strictness_level` influences feedback strategies; stricter clients reject
more deliverables and provide more detailed failure analysis.

---

## Request Lifecycle

Client requests follow an independent state machine from the task lifecycle:

```mermaid
stateDiagram-v2
    [*] --> SUBMITTED
    SUBMITTED --> TRIAGING : intake engine receives
    SUBMITTED --> CANCELLED : rejected at submission
    TRIAGING --> SCOPING : triage complete
    TRIAGING --> CANCELLED : rejected during triage
    SCOPING --> APPROVED : scoping complete
    SCOPING --> CANCELLED : rejected during scoping
    APPROVED --> TASK_CREATED : task created in TaskEngine
    APPROVED --> CANCELLED : rejected before creation
    TASK_CREATED --> [*]
    CANCELLED --> [*]
```

`RequestStatus` is independent from `TaskStatus`. After `TASK_CREATED`, the
task's own lifecycle (CREATED -> ASSIGNED -> ... -> COMPLETED) takes over.

---

## Requirement Generation

Five pluggable strategies implement `RequirementGenerator`:

| Strategy | Approach | Cost | Variety |
|----------|----------|------|---------|
| `TemplateGenerator` | Pattern-based with variable slots | Low | Low |
| `LLMGenerator` | LLM-generated novel requirements | High | High |
| `DatasetGenerator` | Loads from curated corpus | Low | Medium |
| `HybridGenerator` | Dataset seeds + LLM refinement | Medium | High |
| `ProceduralGenerator` | Algorithmic with dependency graphs | Low | Medium |

Each returns `tuple[TaskRequirement, ...]` containing structured requirements
with title, description, type, priority, complexity, and acceptance criteria.

---

## Feedback Strategies

Four pluggable strategies implement `FeedbackStrategy`:

| Strategy | Signal | Use Case |
|----------|--------|----------|
| `BinaryFeedback` | Accept/reject with reason | Simple pass/fail evaluation |
| `ScoredFeedback` | Multi-dimensional scoring | Rich feedback for agent learning |
| `CriteriaCheckFeedback` | Per-criterion pass/fail | Structured failure analysis |
| `AdversarialFeedback` | Deliberately strict/ambiguous | Stress testing and edge cases |

All produce `ClientFeedback` with `accepted` boolean, `reason`, optional `scores`
dictionary, and `unmet_criteria` tuple.

---

## Review Pipeline

The review pipeline walks a chain of `ReviewStage` implementations in order.
Each stage returns a `ReviewVerdict`:

- **PASS**: continue to the next stage.
- **FAIL**: short-circuit; task returns to IN_PROGRESS for rework.
- **SKIP**: stage not applicable; continue to next.

Pipeline progress is tracked in task metadata (not via new `TaskStatus` values).
The task stays in `IN_REVIEW` throughout pipeline execution.

```python
# Metadata tracked on the task during pipeline execution
{
    "review_pipeline": {
        "current_stage": "client",
        "stages_completed": ["internal"],
        "stage_results": {
            "internal": {"verdict": "pass", "reason": null},
            "client": {"verdict": "fail", "reason": "Missing tests"}
        }
    }
}
```

### Built-in Stages

- **InternalReviewStage**: wraps existing `ReviewGateService` logic.
  Backward-compatible default first stage.
- **ClientReviewStage**: invokes `ClientInterface.review_deliverable()`.
  Maps `ClientFeedback` to `ReviewStageResult`.

---

## Intake Engine

The `IntakeEngine` manages the `ClientRequest` lifecycle from `SUBMITTED`
through `TASK_CREATED`. It routes requests to a configured `IntakeStrategy`:

- **DirectIntake**: pass-through; creates a task immediately from the
  requirement with minimal validation.
- **AgentIntake**: routes to an intake agent (PM/Account Manager) for
  triage, scoping, and approval before task creation.

### Boot wiring

`synthorg.client.runtime_builder.build_client_simulation_runtime`
constructs the `IntakeEngine` (plus a single-stage `ReviewPipeline`
of `InternalReviewStage`) during app construction whenever a
`TaskEngine` is present, and `create_app` attaches the resulting
`ClientSimulationState` so `has_simulation_runtime` is true and the
`/simulations` + `/requests` controllers register. The strategy is
selected from the `simulations` settings namespace
(`intake_strategy` ∈ {`direct`, `agent`}, `intake_model`,
`intake_default_project`) via the bootstrap resolver (env >
registered default); the choices are baked in at startup
(`read_only_post_init`). `intake_default_project` is the project the
intake strategy files tasks into and the real work-entry adapter
stamps on the work item (see [Real work-entry path](#real-work-entry-path)). The default `direct` strategy
makes no LLM calls, so the runtime comes online for an empty company.
A selected `agent` strategy that cannot be satisfied (no provider or
no model) degrades to `direct` with a warning rather than failing
boot.

### Real work-entry path

`POST /requests/{id}/approve` is the real (non-simulated) work-entry
path. On approval the request is walked to `APPROVED` and a
background task runs the `IntakeEntryAdapter`
(`WorkSource.INTAKE`), which maps the `ClientRequest` onto a
`WorkItem` and drives the work pipeline spine (intake -> projects ->
decompose -> solo or team execution). The endpoint returns `202
Accepted` with the `APPROVED` request; the terminal `TASK_CREATED`
or `CANCELLED` state lands asynchronously and is observable via `GET
/requests/{id}` and the request WebSocket channel. Reviewer
`scoping_notes` from a prior `/scope` call are folded into the work
item's intent body so the manual scope flow is preserved.

The adapter is built once the work pipeline is online
(`engine.pipeline.entry.boot.wire_real_intake_entry`, called from
the boot runtime-services hook and the post-setup provider reinit)
and attached to the `AppState.intake_entry_adapter` seam. When no
work pipeline is wired (empty company / no provider) approve returns
`AgentRuntimeNotConfiguredError` rather than minting a task no agent
will run.

The `task_board` source is the sibling work-entry path:
`POST /tasks` routes a board filing through `TaskBoardEntryAdapter`
(`WorkSource.TASK_BOARD`) which builds the `WorkItem` from the
user-submitted title/description/project and drives the same spine.
The endpoint returns `202 Accepted` with a
`TaskBoardSubmissionResponse` envelope (correlation id + echo); the
spine creates the task inside its intake phase and the
spine-created task surfaces on the `tasks` WebSocket channel via
`task.created`. Empty-company / no-adapter returns
`AgentRuntimeNotConfiguredError`. The board's column moves remain
pure status walks of the spine-created task. The adapter is wired
by `engine.pipeline.entry.boot.wire_real_task_board_entry` (same
boot + post-setup hot-swap shape as the intake helper, minus the
project bootstrap since board filings carry their own project) and
attached to the `AppState.task_board_entry_adapter` seam.

The `simulations.intake_default_project` setting (env >
registered default, baked in at startup) names the project the
intake strategy files tasks into and the adapter stamps on the work
item; that project is created at startup if absent so the pipeline's
project-existence check and the created task agree.

---

## Task Source Tracking

Tasks created through client simulation carry a `source` field:

```python
class TaskSource(StrEnum):
    INTERNAL = "internal"      # Created by agent/human within the org
    CLIENT = "client"          # From a client (real or simulated)
    SIMULATION = "simulation"  # From simulation runner
```

This enables filtering and analytics by task origin without affecting the
task lifecycle state machine.

---

## Simulation Runner

`SimulationRunner` orchestrates batch simulation runs:

1. Spawn a pool of clients (AI/human/hybrid mix per `ClientPoolConfig`).
2. Generate requirements via `RequirementGenerator`.
3. Submit requirements to `IntakeEngine`.
4. Wait for task completion via `TaskEngine`.
5. Review deliverables via `ClientReviewStage`.
6. Collect metrics (`SimulationMetrics`).
7. Generate reports via `ReportStrategy`.

`ContinuousMode` provides event-driven always-on simulation with scheduled
requirement generation and review triggers.

### Idempotency

`POST /api/v1/simulations/` registers the run via
`SimulationStore.register_if_absent`, an atomic check-and-insert under the
store's lock. A redelivered request (JetStream redelivery, HTTP 5xx-driven
retry, etc.) carrying the same `simulation_id` returns HTTP 409 Conflict
instead of spawning a second runner that races the first on
`update_status` and corrupts metrics. Clients that supply their own
`simulation_id` get retry safety for free; clients that omit it receive a
fresh UUID per call and never collide.

---

## Configuration

All configuration is composed into `ClientSimulationConfig`:

```python
class ClientSimulationConfig(BaseModel):
    pool: ClientPoolConfig           # Pool size, AI/human/hybrid ratios
    generators: RequirementGeneratorConfig  # Strategy + settings
    feedback: FeedbackConfig         # Strategy + scoring rubric
    report: ReportConfig             # Report style discriminator
    runner: SimulationRunnerConfig   # Concurrency, timeouts
    continuous: ContinuousModeConfig # Interval, max concurrent
```

---

## Configuration & Factories

Each client strategy family has a config discriminator that a factory
function in `synthorg.client.factory` dispatches to the concrete
implementation. Misconfiguration fails loudly: every factory raises
`UnknownStrategyError` (a `ValueError` subclass) on an unknown
discriminator rather than silently falling back to a default.

| Config discriminator | Factory function | Strategies |
|---|---|---|
| `RequirementGeneratorConfig.strategy` | `build_requirement_generator()` | `template` → `TemplateGenerator`, `llm` → `LLMGenerator`, `dataset` → `DatasetGenerator`, `procedural` → `ProceduralGenerator` |
| `FeedbackConfig.strategy` | `build_feedback_strategy(config, *, client_id)` | `binary` → `BinaryFeedback`, `scored` → `ScoredFeedback`, `criteria_check` → `CriteriaCheckFeedback`, `adversarial` → `AdversarialFeedback` |
| `ReportConfig.strategy` | `build_report_strategy()` | `summary` → `SummaryReport`, `detailed` → `DetailedReport`, `json_export` → `JsonExportReport`, `metrics_only` → `MetricsOnlyReport` |
| `ClientPoolConfig.selection_strategy` | `build_client_pool_strategy()` | `round_robin` → `RoundRobinStrategy`, `weighted_random` → `WeightedRandomStrategy`, `domain_matched` → `DomainMatchedStrategy` |
| `adapter` arg (intake entry point) | `build_entry_point_strategy(adapter, *, project_id=None)` | `direct` → `DirectAdapter`, `project` → `ProjectAdapter`, `intake` → `IntakeAdapter` |
| `IntakeConfig.strategy` | `build_intake_strategy(config, *, task_engine, default_project, provider=None, cost_tracker=None)` | `direct` → `DirectIntake`, `agent` → `AgentIntake` |
| `WorkSource` (work-entry adapter) | `build_work_entry_adapter(source, *, work_pipeline, default_project)` | `intake` → `IntakeEntryAdapter`, `task_board` → `TaskBoardEntryAdapter` |

The factories follow the project-wide pluggable-subsystems pattern
(protocol + strategy + factory + config discriminator). No silent
defaults: a misspelled discriminator is a hard error at construction
time, not a runtime surprise during a simulation.

!!! note "Hybrid requirement generator is intentionally excluded from factory dispatch"
    `RequirementGeneratorConfig.strategy="hybrid"` does **not** resolve
    through `build_requirement_generator()`. `HybridGenerator` composes
    multiple underlying generators with weights, so it has no
    single-argument factory; callers must construct it manually with a
    tuple of `(generator, weight)` pairs. Passing `"hybrid"` to the
    factory raises `UnknownStrategyError`: this is a deliberate
    deviation from the other strategies, not an oversight.

---

## Observability

Event constants in `synthorg.observability.events.client` and
`synthorg.observability.events.review_pipeline` cover:

- Client request lifecycle (submitted, triaging, scoped, approved, rejected)
- Client review lifecycle (started, completed, feedback recorded)
- Requirement generation events
- Simulation run lifecycle (started, round completed, completed)
- Review pipeline lifecycle (started, stage completed, completed)
- Intake processing (received, accepted, rejected)
