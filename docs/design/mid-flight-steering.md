---
title: Mid-Flight Steering
description: The operator (or the conversational front door) injects a steering directive (a hint or a redirect) into a project at any point during a long-running agent run; in-flight and newly-spawned agents adopt it at safe boundaries, redirects force a re-plan, and obsolete work is cleanly superseded, with no state corruption. The directive is recorded in the project brain as a plan revision with its rationale.
---

# Mid-Flight Steering

"I steer, it continues." An operator watching a long run can change its
direction without stopping it: "use Postgres not Mongo", "pivot off the
frontend". The directive propagates through every in-flight and newly-spawned
agent on the project, redirects force a re-plan of affected work, and the
now-obsolete tasks are cleanly superseded. Nothing is corrupted because
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
`InterventionKind`: a `HINT` is advisory, a `REDIRECT` forces a re-plan. `PAUSE`
and `KILL` are task-lifecycle interventions handled at the cockpit controller,
not steering.

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
    participant Loop as Agent loop (ReAct / Plan / Hybrid)
    Op->>Svc: issue(project, REDIRECT, text, supersede)
    Svc->>Brain: append_entry(PLAN_REVISION, tag=steering)
    Svc->>Svc: EXPLICIT -> TaskEngine.cancel_task(each)
    Svc-->>Op: directive_id (+ proposal in PROPOSE mode)
    Note over Loop: at each turn boundary
    Loop->>Brain: inbox.pending(project, already_adopted)
    Brain-->>Loop: active directives
    Loop->>Loop: inject directive (wrap_untrusted), mark adopted
    Loop->>Loop: REDIRECT -> record pending replan
    Note over Loop: at next step boundary (Plan / Hybrid)
    Loop->>Loop: consume pending replan -> do_replan()
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

A `REDIRECT` additionally records a checkpointed
`pending_steering_replan_id` on the context. Plan-and-Execute and Hybrid loops
consume it at the next **step boundary** via the existing `do_replan()` and
clear it; a crash between adoption and the step boundary preserves the
pending-replan so the forced re-plan still fires on resume. ReAct has no plan
and ignores the field. The current LLM turn and tool batch always finish
first: there is no mid-tool cancellation.

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
and non-corrupting: soon-to-be-superseded agents may briefly adopt and re-plan,
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

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `cockpit.steering_proposer_enabled` | `false` | Enable the LLM supersession proposer for `PROPOSE`-mode redirects. |
| `cockpit.steering_proposer_model` | (empty) | Model id the proposer calls; empty falls back to the no-op proposer. |
| `cockpit.steering_max_active_directives` | `100` | Cap on active directives listed on the operator board. |
