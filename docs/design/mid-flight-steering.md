---
title: Mid-Flight Steering
description: The operator (or the conversational front door) injects a steering directive (a hint or a redirect) into a project at any point during a long-running agent run; in-flight and newly-spawned agents adopt it at safe boundaries, a redirect additionally interrupts a streaming call so the turn is re-issued with it adopted, and obsolete work is cleanly superseded, with no state corruption. The directive is recorded in the project brain as a plan revision with its rationale.
---

# Mid-Flight Steering

"I steer, it continues." An operator watching a long run can change its
direction without stopping it: "use Postgres not Mongo", "pivot off the
frontend". The directive propagates through every in-flight and newly-spawned
agent on the project; a redirect additionally aborts a streaming call so the
turn is re-issued with the constraint already in context, and the now-obsolete
tasks are cleanly superseded. Nothing is corrupted because
adoption happens only at safe boundaries and cancellation only mutates durable
state the running agent observes cooperatively.

See also: [project-brain.md](project-brain.md) (the durable record and the
read projection), [agent-execution.md](agent-execution.md) (the execution
loops, safe boundaries, and the `CANCELLED` termination reason),
[engine.md](engine.md) (the single-writer `TaskEngine`),
[coordination.md](coordination.md) (the cockpit and graceful shutdown).

## Grain and store

A steering directive is **project-scoped** with optional task/agent narrowing.
By default it targets a project, so every agent working that project adopts it;
optional `narrow_task_ids` / `narrow_agent_ids` restrict it to specific runs
(for example a single-agent hint). The two steerable kinds reuse
`InterventionKind`: a `HINT` is advisory and waits for the next turn boundary,
a `REDIRECT` is mandatory and additionally interrupts an in-flight *streaming*
call so the turn is re-issued with it adopted. A buffered call has no interrupt
point, so it finishes and the redirect is adopted at the boundary that follows;
neither kind triggers a generic re-plan. `PAUSE` and `KILL` are task-lifecycle
interventions handled at the cockpit controller, not steering.

The directive is recorded as a project-brain `PLAN_REVISION` entry tagged
`steering` (see [project-brain.md](project-brain.md)). The operator text is the
entry's rationale; per-kind and narrowing tags discriminate it. The brain entry
is the durable source of truth, so the steering history survives a crash and is
auditable alongside every other plan revision. There is no separate steering
table.

The read path is deliberately memory-independent: in-flight loops read active
directives through the brain **repository**'s `list_current` (a cheap indexed
SQL projection), which is available whenever persistence is connected. The
write path goes through `ProjectBrainService.append_entry` (full provenance:
SQL row, git commit, RAG index), which additionally needs the memory backend.
This asymmetry is why the inbox wires into the boot engine early (persistence
only) while the steering service wires later (after the brain is up).

## Propagation at safe boundaries

```mermaid
sequenceDiagram
    participant Op as Operator / Chief of Staff
    participant Svc as SteeringService
    participant Brain as Project Brain
    participant Loop as Agent loop (ReAct)
    Op->>Svc: issue(project, REDIRECT, text, supersede)
    Svc->>Brain: append_entry(PLAN_REVISION, tag=steering)
    Svc->>Svc: EXPLICIT -> TaskEngine.cancel_task(each)
    Svc-->>Op: directive_id (+ proposal in PROPOSE mode)
    Note over Loop: at each turn boundary
    Loop->>Brain: inbox.pending(project, already_adopted)
    Brain-->>Loop: active directives
    Loop->>Loop: inject directive (wrap_untrusted), mark adopted
    Note over Loop: mid-call, streaming only
    Loop->>Loop: REDIRECT pending -> abort call, re-issue turn
```

The propagation reuses the stagnation inject template. At each **turn
boundary**, before the LLM call, the loop asks the steering inbox for active
directives not yet in `ctx.adopted_steering_ids`, injects each as a `USER`
message wrapped with `wrap_untrusted(TAG_BRAIN_STATE, ...)`, and records the id
as adopted. Because the check runs at the top of the loop, a freshly-spawned
agent adopts the constraint before its first decision, so "new agents seed the
constraint" and "in-flight agents adopt at the next boundary" are the same
mechanism.

Consume-once is **context-local**: the adopted-id set travels with the
checkpointed `AgentContext`, never a row or brain-status flag, so a crash and
resume re-injects nothing already adopted, yet every concurrent agent on the
project still adopts the same directive independently. The brain status
(`ACTIVE` / `SUPERSEDED`) is the project-lifecycle axis, orthogonal to
per-execution adoption.

A `REDIRECT` differs from a `HINT` in urgency rather than in machinery: both
are injected and adopted the same way, but a REDIRECT is worth interrupting an
in-flight LLM call for (see below) while a HINT waits for the turn boundary.
The tool batch always finishes first: there is no mid-tool cancellation.

The in-flight **LLM call** is interruptible when the streaming work loop is
active (`engine.work_loop_streaming_enabled` and the model advertises
`supports_streaming`). Consuming `provider.stream()`, the loop polls the
cancellation checker and the steering inbox between chunks: a terminal task
status aborts the call and terminates `CANCELLED`, and a pending `REDIRECT`
aborts the call and re-issues the turn with the directive adopted (the aborted
turn is not recorded, but the partial token usage the stream surfaced is folded
into the run's cost so a discarded call is never under-counted). When streaming
is off or unsupported the loop falls back to the buffered `complete()` call,
where the current LLM turn always finishes first and steering is adopted only at
the next turn boundary.

## Superseding obsolete work

Cancelling a task only mutates durable state; it does not by itself stop a
running agent. So steering pairs the cancel with a cooperative halt:

- **Supersede modes.** `NONE` cancels nothing. `EXPLICIT` cancels the
  operator-supplied task ids synchronously inside `issue()` through the
  single-writer `TaskEngine`, referencing the directive in the cancel reason.
  `PROPOSE` runs a pluggable `SteeringSupersessionProposer` that refines the
  obsolete set and returns it for the operator to confirm or edit via the
  supersede endpoint before anything is cancelled. The agent never cancels
  autonomously.
- **Cooperative halt.** A per-task `TaskCancellationChecker`, consulted at the
  top-of-turn safe boundary (throttled once per turn), reads the task's
  terminal status through the `TaskEngine`. The durable DB status is the
  cross-process signal: the operator cancels in the API process while the agent
  runs in the worker process. On an observed terminal status the loop returns
  `TerminationReason.CANCELLED`; the post-execution pipeline performs no
  re-transition because the task is already terminal (no phantom transition, no
  version conflict).

The `PROPOSE` window between `issue()` and the operator's confirm is accepted
and non-corrupting: agents about to be superseded may briefly adopt and re-plan,
which is transient contradictory work, not corruption. `EXPLICIT` mode cancels
synchronously to shrink that window.

## The front door

`SteeringService.issue(...)` is the single write path; both the operator
(cockpit) and the conversational Chief-of-Staff flow call it. Operator-direct
issuance is immediate; the conversational path routes through
`ApprovalSource.CONVERSATIONAL_INTAKE`.

- **REST.** `SteeringController` at `/cockpit/steering`: `POST` to issue, `GET`
  (by `project_id`) to list active directives for the operator board, and
  `POST /{directive_id}/supersede` to confirm a refined obsolete set. Writes
  require write access; the controller 503s until the steering service wires.
  The operator text is stored raw in the brain; the prompt-safety envelope is
  applied at each LLM sink (the loop wraps on re-injection, the proposer wraps
  candidate task data), so the controller does not double-wrap.
- **MCP.** The cockpit domain exposes `steer`, `steer_supersede` (admin
  guardrails), and `steer_list`, routing through the same service.
- **WebSocket.** `SteeringService` publishes `steering.directive.issued`,
  `steering.supersession.proposed`, and `steering.tasks.superseded` on the
  cockpit channel via a notifier closed over the channels plugin. Directive
  adoption is a worker-side observability event (`steering.directive.adopted`);
  it is not published to the in-memory cockpit channel because the worker runs
  in a separate process.

## Boot wiring

The read path and the write path wire at different times because of their
different dependencies:

- The **steering inbox** is built from `persistence.project_brain` and injected
  into the boot `AgentEngine` during the runtime-services startup step
  (persistence is the only requirement).
- The **steering service** wires in `_wire_steering_service`, which runs after
  `_wire_project_brain` in the feature-wiring chain because it records through
  the memory-gated `ProjectBrainService`. It is gated on the brain service, a
  task engine, and persistence; the pluggable proposer is selected behind
  `cockpit.steering_proposer_enabled` plus a model id. A missing brain leaves
  the steering controllers and MCP tools to 503 rather than poisoning startup.

The cockpit slice is partial-wired (not swapped) so the construction-phase
steering notifier and the later steering service coexist on the same slice.

## What the cockpit reads, and when

Steering is only useful against a live picture, and `CockpitService` builds
that picture from two stores because neither answers the whole question.

While an agent still holds a task, the answer is its **live**
`AgentRuntimeState` row: its own turn count, spend, and when it last did
anything. Once the run has finished, the answer is the **recorded**
flight-recorder frames, which are built from a completed run. Reading the
frames alone (the shape this replaced) meant every in-flight row reported
`turn 0` and zero spend, because the store that answers for a finished run
has nothing to say about one still going. Neither the stuck nor the runaway
marker could fire on the work they exist to catch.

The row is keyed by agent, so the read discards one naming a different task,
and treats an IDLE row as nothing running. Spend is the recorded executions
**plus** the one in flight: a retry starts a new execution at zero while
`budget_limit` is per task, so reading the live figure alone would let a task
that already burned its budget read healthy for the whole of its next
attempt.

Two derived markers, both from operator settings:

- **stuck**: nothing has driven the task since `cockpit.stuck_idle_threshold_minutes`.
  No activity at all is the strongest evidence, not an exemption, so a row
  with no timestamp falls back to the task's filing time. That measures time
  in the QUEUE, which is why a row is written at dispatch: a running task
  carries a live timestamp from pickup, leaving filing time to describe only
  a task no run has claimed.
- **runaway**: spend has passed `cockpit.runaway_cost_percent` of the task's
  `budget_limit`.

### A live run that drives no task

The scan above is keyed on task status, so it sees an agent only while a task
it holds reads `IN_PROGRESS` or `BLOCKED`. Real work falls outside that and was
invisible: a decomposition planning session runs as a staffed agent, for turns,
against a real bill, and drives no task row at all, because the objective it is
planning stays at `CREATED` until dispatch. A live run showed the org planning
for 54 minutes under the heading "Nothing is running".

Those runs get a row of their own, built from the live agent-state row alone,
which is the whole answer for them: the turn count, the spend and the last
activity are all written there per turn. `AgentActivity.task_id` and `.status`
are therefore nullable, and the dashboard says what is true of the RUN rather
than borrowing a status from a task that does not exist.

Two things follow. **Runaway is never flagged** on such a row: the marker
compares spend against the TASK's `budget_limit`, and with no task there is no
bound to compare to, so a fabricated one would mark healthy work as
overspending. And **Pause and Kill are absent** from it, because both controls
address a task id the row does not carry.

Deduplication against the task scan is keyed on the EXECUTION, not the agent:
one agent can hold a task and a planning session at once, and keying by agent
drops the session the moment its owner also has work.

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `cockpit.steering_proposer_enabled` | `true` | Enable the LLM supersession proposer for `PROPOSE`-mode redirects. |
| `cockpit.steering_proposer_model` | (empty) | Provider+model reference (`MODEL_REF`, carries both); empty falls back to the no-op proposer. A non-empty value must bind both provider and model. |
| `cockpit.steering_max_active_directives` | `100` | Cap on active directives listed on the operator board. |
| `cockpit.steering_propose_candidate_limit` | `100` | Per-status cap on in-flight candidate tasks gathered for a `PROPOSE`-mode refinement, bounding the proposer's prompt budget. |
