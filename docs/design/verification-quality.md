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
| `after_rollup` | After Phase 6 | Extension point; no default occupant |
| `before_update_parent` | Before Phase 7 | Authority deference scan |

Default chain: `clarification_gate`, `task_ledger`, `plan_review_gate`, `authority_deference_coordination`.

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
  self-completes without a human; off, the review is opened and decided by a human. It is
  on by default so the review pipeline (the completion oracle included) runs
  automatically rather than parking every task in `IN_REVIEW` for a human. The
  setting only decides whether the pipeline runs *automatically*: the oracle gate
  (see below) enforces on both paths, since a human approval still invokes the same
  gate through `complete_review`.

Beyond the review pipeline, the lifecycle exposes additional human gates that all
route through the same `signal_resume_intent` approvals-resume path. The
**plan-approval gate** (`ApprovalSource.PLAN_REVIEW`,
`coordination.plan_approval_required`, off by default) persists a decomposed team plan
as a durable, versioned, human-editable `Plan` entity and parks an approval referencing
it before any team builds, so an operator can review, rework, or send the plan back for
changes through the `/plans` API and Plan Review workspace before approving (see
[Plan Review](plan-review.md)). The other two are **on by default**, because an agent
that cannot proceed without a human's answer should ask rather than guess: the
**mid-task clarification pause** (`AWAITING_INPUT`, `engine.clarification_enabled`) lets
an agent ask a human an open-ended question, and the **project-decision gate**
(`engine.scoping_enabled`) puts a mid-build implementation fork to a human, who picks
structurally from the agent-supplied options (each with a trade-off write-up, one
recommended) and records the choice as a project-brain `DECISION` entry. Both carry a
declared reversibility and are answerable in the unified conversation as well as in the
approvals queue (see [The Org Asks](org-questions.md)).

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
`_shadow_mode`, `_min_stakes`) are hot-reloadable: an edit rebuilds the runtime
and re-attaches the gates to the persistent review service on the next task, no
restart. Who reviews is not among them, because it is not a setting: the gate
selects a roster holder per review, so a role assignment takes effect on the
next task without any reload at all.

### Layer 1: execution-grounded build/test gate

A deterministic gate (`engine/completion_oracle/evaluator.py`
`BuildTestOracle`) that is a pure function of a task's grounding
classification and its already-persisted `CodeExecutionRecord`s (the
`purpose="tests"` rows), so it needs no new persistence.

Those rows are written from what the agent **ran**, not from what it
declared. A shared classifier (`tools/_test_run_capture.py::is_test_run`)
recognises a test command by its shape (`pytest`, `go test`, `cargo test`,
`npm test`, `gradle ... test`, and the rest), and both `code_runner` and
`shell_command` record through it. Letting the model's own `purpose`
argument arm the gate meant an agent that ran its suite through
`shell_command`, or through `code_runner` without setting the flag,
produced a green suite and zero evidence: the oracle then correctly failed
closed and blocked a build that genuinely passed. The gate's verdict is not
supposed to depend on which tool the model happened to pick, and
model-supplied input is the wrong thing to let decide whether a gate has
evidence at all.

The command is untrusted too, so recognition reads the invoked program and
its verb rather than searching the line for a word. A compound command is
refused outright (`pytest || true` exits 0 whatever the suite did), and a
package manager's `test` counts only as its own subcommand or the script
`run` names: `test` is a real npm package, so `npm install test` succeeds
and would otherwise mint passing evidence for a command that ran no tests.
Build tools (`mvn`, `gradle`, `make`) keep positional target matching,
because their arguments are phase names rather than verbs.
`classify_grounding_requirement` marks a task REQUIRED when it declares (or
produced) a CODE / TESTS artifact; a docs / plan / decision task is
NOT_APPLICABLE and the oracle abstains. The verdict uses NEWEST-run semantics
(the newest test run decides), so a task that failed, was reworked, and now
passes is VERIFIED rather than blocked forever. A REQUIRED task whose newest
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
transient REVIEW task carrying the reviewed task's own stakes and complexity),
not a single `complete_*` call, mirroring the red-team gate's shape. It is an
**ordinary roster agent holding the built-in `Completion Reviewer` role**,
selected per review (see [Selecting the reviewer](#selecting-the-reviewer)).
The reviewer reads the deliverable, may build it and run its tests, and files
exactly one verdict (APPROVE / APPROVE_WITH_NOTES / REJECT / ESCALATE) via the
single terminal tool `submit_completion_oracle_verdict`, guarded by a
trusted-runtime-context contextvar so the reviewer cannot be spoofed into
filing under a different execution and cannot spoof who reviewed whom (the
identities are seeded by the gate, not taken from the tool arguments). The
untrusted deliverable / criteria are wrapped with `wrap_untrusted` at the
prompt boundary (SEC-1).

#### Selecting the reviewer

The reviewer is a roster agent holding the `Completion Reviewer` role, chosen
per review. It is never an identity built at boot from the catalogued role:
such a thing is registered nowhere, staffed by nobody, and absent from
`GET /agents/active`, so "peer review" would be performed by something that is
not a peer, holding a role no operator could grant, and producing verdicts
comparable with nothing.
`scripts/check_no_synthetic_agent_identity.py` is what keeps it out.

#### A gate role is staffed, and it is still not an executor

Holding a gate role confers judging authority, and being staffed is what makes
that authority real. The two facts pull in opposite directions everywhere a
roster is read: the reviewers ARE on the roster, so any rule that lists staffed
roles without asking what a role CONFERS hands the planner a judge to assign
work to.

That is what shipped. `DecompositionContext.available_roles` became the enum of
the `required_role` field, which is required on every subtask, and the gate role
led the list:

```json
"required_role": {
  "type": "string",
  "enum": ["Completion Reviewer", "Developer"],
  "description": "The role accountable for this item, selected from the roles this organisation staffs."
}
```

A live run assigned `Completion Reviewer` to 19 of 102 subtasks, 7 of them
atomic and due to execute, with titles like "Acceptance suite (verification
gate)". Nothing rejected it: the no-self-review invariant is a `CHECK` on a
verdict ROW, and this happens a layer earlier, while the plan is being written.

`engine/decomposition/context.py::roster_from_agents` excludes gate roles, and
it is the only place that can: `_role_field`, `_roster_guidance` and
`describe_unroutable_role` are each pure functions over the roster they are
handed, so all three inherit the answer rather than re-deciding it.
`scripts/check_gate_roles_not_assignable.py` holds the tree to one derivation of
that roster, across `evals/` as well as `src/synthorg/`, because the rule first
shipped enforced in the product and bypassed in the harness measuring it: the
recursion-depth sweep's own `SweepRoster.roles` carried a second copy of the
comprehension, over builders and reviewers alike, and fed it straight to the
sweep's planner. The consequence there is worse than a mis-assigned item, since
plan-level verification landing in both arms contaminates the gated-versus-
ungated contrast at source.

Selection lives in `hr/role_staffing.py`, shared with the red-team gate so the
two cannot drift, and the rule is declared and logged on every call:

1. **Candidates** are ACTIVE holders of the role, minus the executor. The
   exclusion here is a convenience; the invariant stays structural (below).
2. **Reach**: holders who already worked the reviewed initiative are preferred,
   read from the tasks that left the queue on it
   (`engine/initiative/contributors.py::initiative_contributors`, which drops
   the statuses proving no execution happened, so an assignment nobody has
   started confers no preference) rather than from anything stored on the
   project. When the reviewed work names an
   initiative with contributors and none of them qualify, the search widens
   org-wide and logs `hr.staffing.widened` with the project and the reason, so
   a widening away from a set that existed is never silent. Work on no project,
   or on one nobody has worked yet, has no narrower set to widen from, so there
   is nothing to report. A momentarily unreadable task store costs only this
   preference, never the selection. The two roles are declared gate roles
   (`core/role_catalog.py::role_is_gate_role`): quality assurance judges work
   across the org rather than being confined to the initiative it happens to
   have contributed to. It is a property of the role an operator can see and
   grant, never a flag on the identity.
3. **Capability fit** against what the reviewed TASK needs (its stakes and
   complexity, judged by the single org-wide `CapabilityPolicy`, so the bar is
   the same one selection applied to the work in the first place and an
   operator's tuned floors reach it too): an exact rung first, failing that the
   nearest
   HIGHER rung, failing that the nearest LOWER rung, logged as
   `hr.staffing.under_capability` naming both rungs. The agent's rung comes
   from the model catalogue rather than the roster's cached claim, so a
   re-graded model is judged as it is now. A pair nothing grades counts below
   every rung, so it never silently outranks a graded one. Ties break on the
   agent id, so the choice is reproducible.

Capability decides WHO reviews and never what model anybody runs. There is no
reviewer-model setting to inherit or fall back to: the selected agent's own
bound `(provider, model)` pair IS the dispatch target, and nothing on this path
rewrites it.

#### What "the deliverable" is

The reviewed deliverable is the **content of the files the task produced at
its declared paths**, with the agent's closing message alongside rather than
instead. `engine/artifacts/deliverable_content.py` reads each
`ExpectedArtifact.path` inside the task's project workspace and
`engine/review_gate_inputs.py` assembles the two into one SEC-1 fenced block
(`wrap_untrusted(TAG_TOOL_RESULT, ...)`), which every downstream consumer of
the review input shares: the peer reviewer, the red-team gate, and the
output-policy observation.

The files travel typed as well as composed (`RedTeamReviewInput
.produced_artifacts`), read once and used for both, because a consumer asking
a per-file question cannot get the files back out of the composed JSON
document without parsing the thing it was just handed.

Reviewing only the closing prose meant an APPROVE said the agent wrote a
convincing summary, not that the deliverable builds. This is the single most
load-bearing gate in the chain (fail-closed, on by default,
`min_stakes=low`), so it reads the thing it is approving.

The closing message comes from the **run being judged**, passed in by the
caller that is holding it (`attempt_deliverable`, bound onto the builder for
that one review). Asking the flight recorder instead gives the question two
owners, and the second is an observability store: a recorder that stored
nothing makes delivered work indistinguishable from an agent that produced
nothing, which sends it to rework as empty, and a checkpoint-resumed attempt
is answered for by the pre-recovery FAILED attempt, whose turns are the
highest ones recorded. The recorder remains the fallback for a review with no
run in hand (a later, detached read), and there it is a real dependency:
with `cockpit.flight_recorder_enabled` off, or
`cockpit.flight_recorder_sink_strategy` set to `noop`, no frame exists, the
builder returns `None` and the gate applies its `on_missing_deliverable`
posture instead of reviewing content. That path logs at WARNING, because it
is a fact about the system rather than about the task.

Size is bounded by two live settings, so an operator can tune what the
reviewer receives without a restart: `engine.review_artifact_max_chars_per_file`
(default 20000) and `engine.review_artifact_max_chars_total` (default 60000).
Truncation, omission, an absent path, a directory, and an unreadable file
each produce an explicit note in the assembled text rather than silently
shrinking the deliverable, because a reviewer that cannot tell "empty" from
"not shown" cannot judge either.

The reviewer-is-distinct invariant is enforced at three layers: a
`CompletionOracleReport` model validator, the gate's structural resolution,
and a row-level `CHECK (executor_agent_id != reviewer_agent_id)` on the
`completion_oracle_reports` archive table (the twin of the `decision_records`
CHECK). Drawing reviewers from a roster where any agent can hold any role makes
the row-level check matter more, not less: it is the layer that still holds
when something upstream lies. Each verdict is archived (failure-tolerant) in
that append-only, dual-backend table so an operator can answer "why was this
deliverable sent back?" long after the run; an archive-write failure is logged
but never blocks or alters the verdict (fail-OPEN, the one fail-open path in an
otherwise fail-closed gate).

#### Comparable verdicts

A row is one review EVENT, not one execution: a task decided, re-opened and
decided again is reviewed twice and archives twice, so the table carries a
surrogate `report_id` and the read order closes on it.

Alongside the reviewer and executor ids, each row records the
`(reviewer_provider, reviewer_model_id, reviewer_capability)` the review
actually ran on. The reviewer's *current* roster binding is not evidence of
what ran months ago, and without the pair on the row "verdict quality per
model" has nothing to group by. All three are nullable: a row written before
the columns existed genuinely does not know, and NULL is the honest value there
rather than a fabricated attribution.

`GET /completion-oracle/reports` reads the archive (filters: `execution_id`,
`task_id`, `verdict`, `reviewer_agent_id`; cursor-paginated), and
`GET /completion-oracle/reports/summary` counts a reviewer's verdicts by kind
at the storage layer, because a tally over one page would report a window as a
total. `GET /red-team/reports` and its `/summary` are the exact twins. On the
dashboard an agent holding either gate role gains a verdicts panel on its
detail page, so its record sits beside the rest of its work.

### Fail-CLOSED posture and mapping

Unlike the red-team and vision gates, which fail OPEN so a verifier defect can
never block completion, the peer-review gate **fails CLOSED**: a dispatch
failure, a missing verdict, or an unresolvable distinct reviewer yields an
ESCALATE verdict, never a silent pass. The two failing verdicts part company
at that point, because they ask different things of different people: a REJECT
reroutes the task to IN_PROGRESS rework with the reviewer's summary as the
reason, which the agent acts on, while an ESCALATE parks it at BLOCKED with
`blocked_reason=oracle_escalated`, which a human acts on. The human's answer
rejoins the review it came from over the `BLOCKED -> IN_REVIEW` edge, and the
judge is not re-run on it: re-judging the answer re-escalates and discards the
decision the escalation existed to obtain. The reason is what makes that skip
safe, since a coordination wave releasing a subtask parks a task at BLOCKED
too, and keying on the status alone would exempt it from review it never had.
An unstaffed park (`reviewer_unstaffed` / `red_team_unstaffed`) is the same
distinction from the other direction: nobody answered it, so it MUST be
re-judged once the role is filled, which is why it carries its own reason
rather than borrowing the escalation's. The two staffing reasons stay apart
from each other for the same kind of reason: filling one role releases nothing
parked on the other.
APPROVE / APPROVE_WITH_NOTES lets completion proceed. `completion_oracle_min_stakes`
(default `low`, so every task is reviewed) gates the expensive agent-session
review; the deterministic build/test gate runs regardless of it.
`completion_oracle_shadow_mode` runs the reviewer and surfaces the verdict
without enforcing it, for an observation period before enforcement.

### Nobody holds the role

There is no reviewer-model setting: `engine.completion_oracle_reviewer_model`
is retired. A roster agent already names its own `(provider, model)` pair, so a
second setting deciding "which model reviews" would be a second owner for a
decision that has one. The only way peer review can be unavailable is
"unstaffed because nobody holds the role", which is visible in the roster and
fixable through the ordinary role-assignment surface.

Unstaffed is **fail-closed and says so**. The gate returns an ESCALATE verdict
whose summary names the condition, logs
`completion_oracle.review.reviewer_unstaffed` with the role, project, executor
and candidate count, and parks the task at BLOCKED with
`blocked_reason=reviewer_unstaffed`. That reason is load-bearing rather than
decorative: the human-answered skip keys on `oracle_escalated` alone, so an
unstaffed park is re-judged once the role is filled instead of being read as a
decision somebody already made. The built-in identity fallback is the tempting
answer here and is exactly the regression this design removed.

Two things then happen without an operator watching:

- **A hire is requested**, once per unstaffed role org-wide and
  approval-gated. Nothing hires itself: the request opens an `ORG_HIRE`
  approval item and a human decides. Approving it now genuinely instantiates
  and registers the agent, which it did not before: the tail from "human
  approves" to "agent exists" was unreachable, so auto-hire would have been
  theatre.
- **The park heals**, level-triggered. `engine/review_staffing/reconciler.py`
  sweeps tasks parked on either staffing reason and walks each one
  `BLOCKED -> IN_REVIEW` once an eligible holder exists, so the review runs
  properly rather than being waved through. Every pass logs what it moved
  and what it left, and a park that persists says why. The sweep is periodic
  (`engine.review_staffing_resync_interval_seconds`, default 900) and is
  additionally nudged when the roster changes, so a hire that lands is picked
  up immediately rather than at the next tick.

## Order of Operations

Quality and approval surfaces operate at distinct points in the task
lifecycle: the verification stage, the review pipeline, the mid-execution
`AUTH_REQUIRED` park, the post-completion `IN_REVIEW` gate, the completion
oracle (build/test then peer review), and the adversarial red-team gate.

| Phase | Surface | Trigger | Task status during | Exit | Where documented |
|-------|---------|---------|--------------------|------|------------------|
| Mid-execution | `AUTH_REQUIRED` park | Agent calls a tool that requires approval at runtime (e.g. `deploy`, `db:admin`). Driven by `ApprovalGate` middleware. | `AUTH_REQUIRED` | Approved: returns to `ASSIGNED`. Denied / timeout: `CANCELLED`. | [Security: Approval Workflow](security.md#approval-workflow) |
| Agent done | Verification stage | Workflow blueprint has a `VERIFICATION` control-flow node. Runs as a separate evaluator agent with its own context. | `IN_PROGRESS` (engine-internal) | Pass: continue to next node. Fail: regenerate. Refer: hand to human via `VERIFICATION_REFER` edge. | This page; [Workflow Node and Edge Types](#workflow-node-and-edge-types) |
| Agent done | Review pipeline | Task transitions `IN_PROGRESS` to `IN_REVIEW`. Chain of `ReviewStage` instances runs. | `IN_REVIEW` | First-failing stage returns the task to `IN_PROGRESS`; all-pass moves to `COMPLETED`. | This page, [Review Pipeline](#review-pipeline) |
| Review pipeline PASS | Completion oracle gate | On by default (`engine.completion_oracle_enabled`). Two composed gates, first in the chain: the deterministic build/test gate (always) and the agent-session peer reviewer (when `task.stakes >= completion_oracle_min_stakes`, default `low`). Fires on both the auto-review and human-approve paths. | `IN_REVIEW` | Build/test `BUILD_TEST_FAILED` / `UNVERIFIED` (fail-CLOSED) or reviewer REJECT: routes back to `IN_PROGRESS` with the reason. Reviewer ESCALATE: parks at `BLOCKED` with `blocked_reason=oracle_escalated`, because it asks a human rather than requesting rework; the answer rejoins the review through `BLOCKED -> IN_REVIEW`. Nobody holding the Completion Reviewer role: parks at `BLOCKED` with `blocked_reason=reviewer_unstaffed`; the staffing reconciler opens the approval-gated hire, releases the park and re-drives the review once a holder exists. VERIFIED + APPROVE: proceeds. Shadow mode: verdict surfaced, not enforced. | This page, [Completion Oracle Gate](#completion-oracle-gate) |
| Completion oracle PASS | Output-style observation | Deterministic (no LLM), always on when the policy is wired and enabled. NOT a gate: it reads the produced files, one per declared path, and reports what still carries a hard-rule violation. Style is enforced in-session at the tool that wrote the file, where the agent can fix it on its next turn; here the session has ended and the only correction left would be a whole re-dispatch. | `IN_REVIEW` | Always the prior verdict, unchanged. It returns no outcome, so it cannot reroute, un-approve or fail a task. A blocking finding emits one `output_style.backstop.observed` WARNING. | [Output-Style Policy](output-style-policy.md) |
| Completion oracle PASS | Red-team gate | Opt-in (`CompanyConfig.security.red_team.enabled`) AND stakes-gated: fires when the review pipeline returns its COMPLETED verdict and the completion oracle has not blocked, BEFORE the task-engine transition lands, only when `task.stakes >= engine.red_team_min_stakes` (default `HIGH`). | `IN_REVIEW` | BLOCK: routes back to `IN_PROGRESS` with the red-team summary as the rework reason. PASS / PASS_WITH_FINDINGS: pipeline's verdict stands. Nobody holding the Red Team role: parks at `BLOCKED` with `blocked_reason=red_team_unstaffed`; the staffing reconciler opens the hire, releases the park and re-drives the review once a holder exists. Below the stakes threshold: SKIP (logs `RED_TEAM_GATE_SKIPPED`), pipeline's verdict stands. | [Security: Adversarial Red-Team Gate](security.md#adversarial-red-team-gate) |
| Red-team gate PASS | Vision verifier gate | Opt-in (`CompanyConfig.security.vision_verify.enabled`). The UI cousin of the red-team gate: fires after the red-team gate for GUI deliverables that carry screenshots (`vision_input`). Pluggable `VisionVerifier` (`noop` / `heuristic` / `llm_vision`) judges whether the running app matches the brief. | `IN_REVIEW` | BLOCK: routes back to `IN_PROGRESS` with the vision summary as the rework reason. PASS / PASS_WITH_FINDINGS: prior verdict stands. Absent screenshots: SKIP (non-GUI deliverable). | This page, [Vision Verifier Gate](#vision-verifier-gate) |
| Human decision | Review-gate decision | A human approves/rejects the parked review item via `ReviewGateService.complete_review`. Both a completed run (`review:task_completion`) and a **failed** run (`review:task_failed`) reach the queue. | `IN_REVIEW`, `BLOCKED` (oracle escalation) or `FAILED` | Completed: approve `IN_REVIEW -> COMPLETED`, reject `IN_REVIEW -> IN_PROGRESS`. Escalated: the decision walks `BLOCKED -> IN_REVIEW` first, so `COMPLETED` stays reachable only through the review the oracle guards. Failed: approve **acknowledges** (stays `FAILED`), reject **retries** `FAILED -> ASSIGNED`. | [Security: Failed-run review decisions](security.md#failed-run-review-decisions) |

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
