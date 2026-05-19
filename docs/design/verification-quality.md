---
title: Verification & Quality
description: Verification stage, harness middleware layer, review pipeline, and intake engine. Quality scoring, rubric grading, and criteria decomposition.
---

# Verification & Quality

!!! warning "Designed behaviour; runtime in active development"

    This page is the source of truth for the **designed** behaviour of this subsystem. The intake engine is online: the real work-entry path (`POST /requests/{id}/approve`) drives an approved request through the pipeline spine so an agent executes it. The verification stage runs with the agent runtime, which is in active development (see the [Roadmap](../roadmap/index.md)); that code is built and unit-tested as components but not yet exercised by a live verification agent.

This page covers the quality-assurance pipeline attached to agent output: the verification stage that runs after an agent completes a task, the harness middleware that wraps every agent invocation, the review pipeline that validates produced artifacts, and the intake engine that ingests new work.

## Verification Stage

Verification is a first-class stage in the workflow engine. Three converging research sources (Marco DeepResearch on verification-centric agent frameworks, GEMS on the five-stage agent loop with explicit Verifier, and the Anthropic three-agent harness with Planner/Generator/Evaluator and calibrated grading) all converge on verification as a **separate agent with its own context**, not a self-evaluation inside the generator step.

### Workflow Node and Edge Types

`WorkflowNodeType.VERIFICATION` is a control-flow node like `CONDITIONAL`. Three dedicated edge types route verification outcomes:

- `VERIFICATION_PASS`: artifact accepted
- `VERIFICATION_FAIL`: artifact rejected, routed to regeneration
- `VERIFICATION_REFER`: confidence below threshold, escalated to human review

Blueprint validation enforces exactly one of each edge type per verification node.

### Calibrated Rubric Grading

Each verification node references a `VerificationRubric` by name. A rubric contains:

- **Criteria** (`RubricCriterion`): weighted dimensions with `binary`, `ternary`, or `score` grade types
- **Calibration examples**: few-shot demonstrations for LLM graders
- **Minimum confidence**: below this threshold, the verdict is overridden to `REFER`

Built-in rubrics: `frontend-design` (four criteria: design/originality/craft/functionality) and `default-task` (correctness/completeness/probe-adherence).

### Atomic Criteria Decomposition

Acceptance criteria are decomposed into atomic binary probes (`AtomicProbe`) via a pluggable `CriteriaDecomposer` protocol. The default `LLMCriteriaDecomposer` uses the medium-tier provider. An `IdentityCriteriaDecomposer` maps each criterion to one probe for deterministic testing.

### Structured Handoff Artifacts

`HandoffArtifact` carries the payload, artifact references, probes, and optional rubric between stages. A model validator rejects self-handoff (`from_agent_id == to_agent_id`). Immutability is enforced by the frozen Pydantic model (`frozen=True`).

### Self-Evaluation Rejection

> Self-evaluation (where the generator also judges its own output) is explicitly rejected. Prior research documents that self-evaluation produces over-confidence and fails to catch the generator's own blind spots. `VerificationResult.evaluator_agent_id` MUST differ from the generator agent ID; enforced by model validator at construction.

### Pluggable Grading

The `RubricGrader` protocol follows the standard protocol + strategy + factory + config discriminator pattern (mirroring `engine/classification/`). Variants: `LLM` (production) and `HEURISTIC` (testing/fallback). Configuration via `VerificationConfig`.

---

## Harness Middleware Layer

The engine uses a composable middleware layer for cross-cutting concerns that span agent execution and multi-agent coordination. Two separate protocols serve two distinct pipelines.

### Agent Middleware

Protocol: `AgentMiddleware` (`engine/middleware/protocol.py`). Six async hooks in declared order:

| Hook | Runs | Purpose |
|------|------|---------|
| `before_agent` | Once on invocation | Load memory, validate input, record hashes |
| `before_model` | Before each model call | Trim history, redact PII, inject context |
| `wrap_model_call` | Around model call | Caching, dynamic tools, model swap |
| `wrap_tool_call` | Around tool execution | Inject context, gate tools |
| `after_model` | After model responds | Human-in-loop, assumption-violation checks |
| `after_agent` | Once on completion | Save results, notify, cleanup |

Composition: `before_*` left-to-right, `after_*` right-to-left, `wrap_*` onion-style (each wraps the next). Exceptions propagate to the classification pipeline.

Default chain: `checkpoint_resume`, `delegation_chain_hash`, `authority_deference`, `sanitize_message`, `security_interceptor`, `policy_gate`, `approval_gate`, `assumption_violation`, `classification`, `cost_recording`.

**Optional middleware** (registered in `_AGENT_OPT_IN`, must be enabled explicitly):

- `SemanticDriftDetector` (`after_model` slot): compares model output against task acceptance criteria using cosine similarity. Opt-in via `CompanyConfig.security.semantic_drift_enabled`. Fail-soft: logs warnings but never blocks.

### Coordination Middleware

Protocol: `CoordinationMiddleware` (`engine/middleware/coordination_protocol.py`). Five async hooks:

| Hook | Pipeline Position | Purpose |
|------|-------------------|---------|
| `before_decompose` | Before Phase 1 | Clarification gate |
| `after_decompose` | After Phase 1 | Post-decomposition analysis |
| `before_dispatch` | Before Phase 3-5 | Plan review gate, task ledger |
| `after_rollup` | After Phase 6 | Progress ledger, replan hook |
| `before_update_parent` | Before Phase 7 | Authority deference scan |

Default chain: `clarification_gate`, `task_ledger`, `plan_review_gate`, `progress_ledger`, `coordination_replan`, `authority_deference_coordination`.

### S1 Constraint Hooks

| Middleware | Hook | Behavior |
|-----------|------|----------|
| `AuthorityDeferenceGuard` | `before_agent` | Detects authority cues in transcripts, logs patterns, injects justification header |
| `AssumptionViolationMiddleware` | `after_model` | Detects broken assumptions, emits escalation events |
| `ClarificationGateMiddleware` | `before_decompose` | Validates acceptance criteria specificity |
| `DelegationChainHashMiddleware` | `before_agent` | Records SHA-256 content hash for delegation drift detection |

### Configuration

Per-company: `CompanyConfig.middleware` (`MiddlewareConfig`) with agent and coordination sub-configs.

Per-task: `Task.middleware_override` replaces the company-level chain when set.

### Error Semantics

Middleware exceptions propagate to the classification pipeline. `ClassificationResult.action` decides: retry, escalate, or fail. No silent swallowing.

---

## Review Pipeline

The review pipeline provides a configurable chain of review stages for tasks
in `IN_REVIEW` status. See the [Client Simulation](client-simulation.md) design
page for the full architecture, including `ReviewStage` protocol, pipeline
execution semantics, and metadata tracking.

Key design decisions:

- **No new TaskStatus values** for pipeline tracking; tasks stay `IN_REVIEW`
  throughout, with progress tracked in task metadata.
- **Short-circuit on FAIL**: first failing stage sends the task back to
  `IN_PROGRESS` for rework with the stage name and reason in metadata.
- **Default fallback**: when no pipeline is configured, the existing
  `ReviewGateService` single-stage behavior runs.

## Intake Engine

The intake engine processes `ClientRequest` submissions through an independent
state machine (`RequestStatus`) before creating tasks in the task engine. The
real work-entry path (`POST /requests/{id}/approve`) approves a request and
runs it through the `IntakeEntryAdapter` into the work pipeline spine so an
agent executes it; the terminal state lands asynchronously. See
[Client Simulation](client-simulation.md) for the full request lifecycle,
intake strategy contracts, and the real work-entry path.

---

## Order of Operations

The four quality and approval surfaces (verification stage, review
pipeline, mid-execution `AUTH_REQUIRED` park, post-completion
`IN_REVIEW` gate) operate at distinct points in the task lifecycle.

| Phase | Surface | Trigger | Task status during | Exit | Where documented |
|-------|---------|---------|--------------------|------|------------------|
| Mid-execution | `AUTH_REQUIRED` park | Agent calls a tool that requires approval at runtime (e.g. `deploy`, `db:admin`). Driven by `ApprovalGate` middleware. | `AUTH_REQUIRED` | Approved: returns to `ASSIGNED`. Denied / timeout: `CANCELLED`. | [Security: Approval Workflow](security.md#approval-workflow) |
| Agent done | Verification stage | Workflow blueprint has a `VERIFICATION` control-flow node. Runs as a separate evaluator agent with its own context. | `IN_PROGRESS` (engine-internal) | Pass: continue to next node. Fail: regenerate. Refer: hand to human via `VERIFICATION_REFER` edge. | This page, [Workflow Node and Edge Types](#workflow-node-and-edge-types) |
| Agent done | Review pipeline | Task transitions `IN_PROGRESS` to `IN_REVIEW`. Chain of `ReviewStage` instances runs. | `IN_REVIEW` | First-failing stage returns the task to `IN_PROGRESS`; all-pass moves to `COMPLETED`. | This page, [Review Pipeline](#review-pipeline) |

Key invariants:

- `AUTH_REQUIRED` is the mid-execution park reason and uses the
  `ApprovalGate` middleware in the agent harness. The review pipeline
  is the post-completion quality gate and uses `ReviewGateService`.
  The two are independent: a single task can encounter both (e.g.
  pause for deploy approval mid-task, then enter `IN_REVIEW` once the
  agent finishes).
- The verification stage runs BEFORE the review pipeline when both
  are configured for the same workflow. Verification is a workflow
  blueprint construct (a node in the graph); the review pipeline
  fires on the `IN_PROGRESS` to `IN_REVIEW` transition that happens
  after the workflow's last node completes.
- The review pipeline does not mint new `TaskStatus` values; the
  task stays at `IN_REVIEW` throughout, with stage progress in
  metadata.

---

## See Also

- [Task & Workflow Engine](engine.md): task dispatch, state coordination
- [Agent Execution](agent-execution.md): per-agent execution loop
- [Coordination](coordination.md): multi-agent topology, decomposition
- [Design Overview](index.md): full index
