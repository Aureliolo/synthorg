---
title: Verification & Quality
description: Verification stage, harness middleware layer, review pipeline, and intake engine. Quality scoring, rubric grading, and criteria decomposition.
---

# Verification & Quality

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

### Rubric Grading on the Review Pipeline

The decomposer + grader factories are wired onto the live post-completion path as a `VerificationReviewStage` (`engine/review/stages/verification.py`), which runs first in the review pipeline. It decomposes a task's acceptance criteria into probes, grades the work against a rubric with a *separate* evaluator identity, and maps the verdict onto the pipeline: `PASS`/`REFER` let the task proceed (REFER is surfaced in stage metadata for human review, never a hard fail), `FAIL` bounces the task to `IN_PROGRESS` for rework. A grader fault fails OPEN (the stage `SKIP`s) so a verifier defect never blocks completion. The deterministic default (identity decomposer + heuristic grader) grades the proportion of acceptance criteria marked met, so the stage works without a provider; `simulations.verification_grader` / `verification_decomposer` switch to the LLM variants and `simulations.verification_review_enabled` gates the stage (on by default, baked in at startup).

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

The chain is wired into the engine at boot (gated by `engine.enable_agent_middleware`, on by default): its `before_agent` / `after_agent` hooks fire at the `AgentEngine` execution boundary (`engine/_agent_middleware_run.py`). The live effect today is authority-deference defence: when `AuthorityDeferenceGuard.before_agent` detects authority cues in the conversation, the engine injects its justification header as a system message. The per-call slots (`security_interceptor`, `approval_gate`, `cost_recording`, `classification`) remain ordering placeholders whose real logic stays inline (`ToolInvoker`, the execution loop, `_post_execution_pipeline`) until the chain is also wired into the per-turn model / tool call sites.

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

| Middleware | Hook | Behaviour |
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
  `ReviewGateService` single-stage behaviour runs.
- **Automatic vs human-gated**: `engine.auto_review_on_completion` (default **on**,
  hot-reloadable) controls who acts on a task reaching `IN_REVIEW`. On, the staged
  pipeline runs automatically and applies its verdict so a verified task
  self-completes without a human; off, a human opens the review and decides. It is
  on by default so the review pipeline (the completion oracle included) runs
  automatically rather than parking every task in `IN_REVIEW` for a human. The
  setting only decides whether the pipeline runs *automatically*: the oracle gate
  (see below) enforces on both paths, since a human approval still invokes the same
  gate through `complete_review`.

Beyond the review pipeline, the lifecycle exposes additional human gates that all
route through the same `signal_resume_intent` approvals-resume path, each off by
default: the **plan-approval gate** (`ApprovalSource.PLAN_REVIEW`,
`coordination.plan_approval_required`) persists a decomposed team plan as a durable,
versioned, human-editable `Plan` entity and parks an approval referencing it before
any team builds, so an operator can review, rework, or send the plan back for changes
through the `/plans` API and Plan Review workspace before approving (see
[Plan Review](plan-review.md)); the **mid-task clarification pause** (`AWAITING_INPUT`,
`engine.clarification_enabled`) lets an agent ask a human an open-ended question; and
the **project-decision gate** (`engine.scoping_enabled`) puts a mid-build implementation
fork to a human, who picks structurally from the agent-supplied options (each with a
tradeoff writeup, one recommended) and records the choice as a project-brain `DECISION`
entry.

## Intake Engine

The intake engine processes `ClientRequest` submissions through an independent
state machine (`RequestStatus`) before creating tasks in the task engine. The
synthetic-client work-entry path (`POST /requests/{id}/approve`, a benchmark
door gated off by default behind `simulations.client_intake_enabled`) approves
a request and runs it through the `IntakeEntryAdapter` into the work pipeline
spine so an agent executes it; the terminal state lands asynchronously. See
[Client Simulation](client-simulation.md) for the full request lifecycle,
intake strategy contracts, and the gated work-entry path.

---

## Vision Verifier Gate

The vision verifier is the UI cousin of the adversarial red-team gate: where the
red-team gate attacks a text deliverable, the vision gate judges whether a running
GUI deliverable matches its brief. It is opt-in
(`CompanyConfig.security.vision_verify.enabled`, off by default) and fires after the
red-team gate, before the `IN_REVIEW -> COMPLETED` transition.

A pluggable `VisionVerifier` (`security/visionverify/`) follows the standard
protocol + strategy + factory + config discriminator pattern:

- **`noop`** (default): inert; returns a clean report.
- **`heuristic`**: deterministic, no LLM. Checks structured `VisualExpectation`
  entries (e.g. dominant colour) against the captured screenshots. Used by the
  acceptance test so a brief-mismatch BLOCK is reproducible.
- **`llm_vision`**: sends the screenshots (as multimodal `image_parts`) plus the
  fenced brief to a vision-capable model and parses a structured verdict from a
  tool call. Gated on `ModelCapabilities.supports_vision`.

The `VisionVerifierGate` maps the report's findings to a verdict
(`PASS` / `PASS_WITH_FINDINGS` / `BLOCK`) via the same severity x autonomy routing
matrix as the red-team gate. Self-evaluation is rejected (the verifier identity
must differ from the deliverable's generator). A verifier fault fails OPEN (a
synthetic INFO finding) so a fault never blocks completion. SEC-1: the untrusted
brief / criteria are wrapped with `wrap_untrusted` before reaching the model;
screenshot bytes travel as structured `image_parts`, not as prompt text, and are
elided from the cassette's human-readable copy.

## Completion Oracle Gate

The completion oracle makes "done" mean *the code compiles, its tests pass,
and an independent reviewer approves* rather than "the run produced some
artifacts". It is **on by default** (opt-out via
`engine.completion_oracle_enabled`) and is two composed gates that run first in
the completion chain, before the red-team and vision gates, on every path to
COMPLETED (both the auto-review `run_pipeline` and the human-driven
`complete_review`), because they live on `ReviewGateService`, not on the
auto-review trigger. Its natural home is the autonomous flow: with
`engine.auto_review_on_completion` on by default a verified task self-completes
and the oracle gates that completion; a human opening a review is gated by the
same two gates. All the oracle settings (`completion_oracle_enabled`,
`_shadow_mode`, `_min_stakes`, `_reviewer_model_tier`) are hot-reloadable: an
edit rebuilds the runtime and re-attaches the gates to the persistent review
service on the next task, no restart.

### Layer 1: execution-grounded build/test gate

A deterministic gate (`engine/completion_oracle/evaluator.py`
`BuildTestOracle`) that is a pure function of a task's grounding
classification and its already-persisted `CodeExecutionRecord`s (the
`purpose="tests"` rows the code-runner writes), so it needs no new persistence.
`classify_grounding_requirement` marks a task REQUIRED when it declares (or
produced) a CODE / TESTS artifact; a docs / plan / decision task is
NOT_APPLICABLE and the oracle abstains. The verdict uses LATEST-run semantics
(the newest test run decides), so a task that failed, was reworked, and now
passes is VERIFIED rather than blocked forever. A REQUIRED task whose latest
test run failed (`BUILD_TEST_FAILED`) or that has no passing test evidence
(`UNVERIFIED`, the stub the oracle exists to catch) is routed back to
IN_PROGRESS. This gate **fails CLOSED**: absent, failing, or unreadable test
evidence for a code task blocks; only the *structural* absence of the record
store (a persistence-less boot, `CHECKER_UNAVAILABLE`) passes through.

The build/test verdict is also the source of truth for a run's `RunOutcome`:
`derive_run_outcome` takes an `oracle_blocked` flag so the approvals read
surface shows a code task that does not build as FAILED even when it produced
artifacts, mirroring how EMPTY is resolved at read time.

### Layer 2: agent-session peer reviewer

The independent reviewer is a real agent session (`AgentEngine.run` on a
transient REVIEW task pinned to CRITICAL stakes), not a single `complete_*`
call, mirroring the red-team gate's shape. A built-in `Completion Reviewer`
role in Quality Assurance gives it a stable, non-human-assignable identity
distinct from any executor by construction (defence-in-depth equality-checked
at gate time). The reviewer reads the deliverable, may build it and run its
tests, and files exactly one verdict (APPROVE / APPROVE_WITH_NOTES / REJECT /
ESCALATE) via the single terminal tool `submit_completion_oracle_verdict`,
guarded by a trusted-runtime-context contextvar so the reviewer cannot be
spoofed into filing under a different execution and cannot spoof who reviewed
whom (the identities are seeded by the gate, not taken from the tool
arguments). The untrusted deliverable / criteria are wrapped with
`wrap_untrusted` at the prompt boundary (SEC-1).

The reviewer-is-distinct invariant is enforced at three layers: a
`CompletionOracleReport` model validator, the gate's structural resolution,
and a row-level `CHECK (executor_agent_id != reviewer_agent_id)` on the
`completion_oracle_reports` archive table (the twin of the `decision_records`
CHECK). Each verdict is archived (best-effort) in that append-only, dual-backend
table so an operator can answer "why was this deliverable sent back?" long after
the run; an archive-write failure is logged but never blocks or alters the
verdict (fail-OPEN, the one fail-open path in an otherwise fail-closed gate).

### Fail-CLOSED posture and mapping

Unlike the red-team and vision gates, which fail OPEN so a verifier defect can
never block completion, the peer-review gate **fails CLOSED**: a dispatch
failure, a missing verdict, or an unresolvable distinct reviewer yields an
ESCALATE verdict, never a silent pass. A REJECT or ESCALATE reroutes the task
to IN_PROGRESS rework with the reviewer's summary as the reason; APPROVE /
APPROVE_WITH_NOTES lets completion proceed. `completion_oracle_min_stakes`
(default `low`, so every task is reviewed) gates the expensive agent-session
review; the deterministic build/test gate runs regardless of it.
`completion_oracle_shadow_mode` runs the reviewer and surfaces the verdict
without enforcing it, for an observation period before enforcement. The reviewer
tier is pinned via `completion_oracle_reviewer_model_tier` (default `medium`),
never inheriting the executor's tier.

## Order of Operations

Quality and approval surfaces operate at distinct points in the task
lifecycle: the verification stage, the review pipeline, the mid-execution
`AUTH_REQUIRED` park, the post-completion `IN_REVIEW` gate, the completion
oracle (build/test then peer review), and the adversarial red-team gate.

| Phase | Surface | Trigger | Task status during | Exit | Where documented |
|-------|---------|---------|--------------------|------|------------------|
| Mid-execution | `AUTH_REQUIRED` park | Agent calls a tool that requires approval at runtime (e.g. `deploy`, `db:admin`). Driven by `ApprovalGate` middleware. | `AUTH_REQUIRED` | Approved: returns to `ASSIGNED`. Denied / timeout: `CANCELLED`. | [Security: Approval Workflow](security.md#approval-workflow) |
| Agent done | Verification stage | Workflow blueprint has a `VERIFICATION` control-flow node. Runs as a separate evaluator agent with its own context. | `IN_PROGRESS` (engine-internal) | Pass: continue to next node. Fail: regenerate. Refer: hand to human via `VERIFICATION_REFER` edge. | This page, [Workflow Node and Edge Types](#workflow-node-and-edge-types) |
| Agent done | Review pipeline | Task transitions `IN_PROGRESS` to `IN_REVIEW`. Chain of `ReviewStage` instances runs. | `IN_REVIEW` | First-failing stage returns the task to `IN_PROGRESS`; all-pass moves to `COMPLETED`. | This page, [Review Pipeline](#review-pipeline) |
| Review pipeline PASS | Completion oracle gate | On by default (`engine.completion_oracle_enabled`). Two composed gates, first in the chain: the deterministic build/test gate (always) and the agent-session peer reviewer (when `task.stakes >= completion_oracle_min_stakes`, default `low`). Fires on both the auto-review and human-approve paths. | `IN_REVIEW` | Build/test `BUILD_TEST_FAILED` / `UNVERIFIED` (fail-CLOSED) or reviewer REJECT / ESCALATE: routes back to `IN_PROGRESS` with the reason. VERIFIED + APPROVE: proceeds. Shadow mode: verdict surfaced, not enforced. | This page, [Completion Oracle Gate](#completion-oracle-gate) |
| Completion oracle PASS | Output-style gate | Deterministic (no LLM), always on when the policy is wired. Scans the deliverable prose for a hard-rule violation (the em-dash ban) before the adversarial gates, a defence-in-depth backstop for a deliverable that reached completion by a path that skipped a guarded tool. | `IN_REVIEW` | BLOCK: routes back to `IN_PROGRESS` with the output-style summary as the rework reason. Clean / shadow / exempt: prior verdict stands. Policy unwired or disabled: pass-through. | [Output-Style Policy](output-style-policy.md) |
| Output-style gate PASS | Red-team gate | Opt-in (`CompanyConfig.security.red_team.enabled`) AND stakes-gated: fires when the review pipeline returns its COMPLETED verdict and the completion oracle has not blocked, BEFORE the task-engine transition lands, only when `task.stakes >= stakes_routing.red_team_min_stakes` (default `HIGH`). | `IN_REVIEW` | BLOCK: routes back to `IN_PROGRESS` with the red-team summary as the rework reason. PASS / PASS_WITH_FINDINGS: pipeline's verdict stands. Below the stakes threshold: SKIP (logs `RED_TEAM_GATE_SKIPPED`), pipeline's verdict stands. | [Security: Adversarial Red-Team Gate](security.md#adversarial-red-team-gate) |
| Red-team gate PASS | Vision verifier gate | Opt-in (`CompanyConfig.security.vision_verify.enabled`). The UI cousin of the red-team gate: fires after the red-team gate for GUI deliverables that carry screenshots (`vision_input`). Pluggable `VisionVerifier` (`noop` / `heuristic` / `llm_vision`) judges whether the running app matches the brief. | `IN_REVIEW` | BLOCK: routes back to `IN_PROGRESS` with the vision summary as the rework reason. PASS / PASS_WITH_FINDINGS: prior verdict stands. Absent screenshots: SKIP (non-GUI deliverable). | This page, [Vision Verifier Gate](#vision-verifier-gate) |
| Human decision | Review-gate decision | A human approves/rejects the parked review item via `ReviewGateService.complete_review`. Both a completed run (`review:task_completion`) and a **failed** run (`review:task_failed`) reach the queue. | `IN_REVIEW` or `FAILED` | Completed: approve `IN_REVIEW -> COMPLETED`, reject `IN_REVIEW -> IN_PROGRESS`. Failed: approve **acknowledges** (stays `FAILED`), reject **retries** `FAILED -> ASSIGNED`. | [Security: Failed-run review decisions](security.md#failed-run-review-decisions) |

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
- The gates split on fail policy, and a maintainer must not invert them
  by copy-paste. The red-team and vision gates fail OPEN on an internal
  fault (a verifier defect must never block completion). The completion
  oracle fails CLOSED: the build/test gate blocks a code task it cannot
  confirm builds, and the peer-review gate escalates to a human when no
  distinct reviewer or verdict is resolvable, because its whole purpose
  (an independent reviewer must exist) would otherwise be silently defeated.

---

## See Also

- [Task & Workflow Engine](engine.md): task dispatch, state coordination
- [Agent Execution](agent-execution.md): per-agent execution loop
- [Coordination](coordination.md): multi-agent topology, decomposition
- [Design Overview](index.md): full index
