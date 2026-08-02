# Initiative Tail

The two stages between "every plan item is done" and delivery, and the driver
that replans an initiative which can no longer advance.

[Project Lifecycle](project-lifecycle.md) owns the graph and the rollup that
opens this tail; [Plan Review](plan-review.md) owns everything before dispatch.

## The problem

The general loop ran **plan, execute, verify** and then stopped. Every plan item
passing its own review gate completed the plan, the project, and the objective.

That is a real guarantee about each piece and no guarantee at all about the
whole. An initiative could deliver a set of individually-verified parts that
nobody had ever assembled, run together, or checked against what the objective
actually asked for, and the board would show it as delivered. Three smaller
leaks kept that from even being reliable per piece:

- a WORK plan item could declare zero expected artifacts, which disarmed the
  fail-loud zero-artifact guard, so a chat-only run with no output reached
  review as though it had produced something;
- the lifecycle-only baseline execution service walked any task straight to
  `COMPLETED`, gate or no gate;
- the coordination parent rollup derived subtask status from run outcomes,
  which report success before verification.

## Shape

```mermaid
stateDiagram-v2
    EXECUTING --> INTEGRATING: every item done
    INTEGRATING --> EVALUATING: assembly job passed its review gate
    EVALUATING --> COMPLETED: every success criterion met
    INTEGRATING --> EXECUTING: an item regressed
    EVALUATING --> EXECUTING: an item regressed
    INTEGRATING --> SUPERSEDED: replan
    EVALUATING --> SUPERSEDED: replan
```

**`EXECUTING -> COMPLETED` does not exist.** Neither does the project's
`ACTIVE -> COMPLETED`. Delivery has exactly one predecessor in both machines, so
the tail cannot be skipped by construction rather than by whichever service
happens to be wired. The back-edges carry a regression: an item that stops being
done (integration findings routed back as rework) reopens the build without a
replan.

## INTEGRATE

`engine/initiative/integrate.py` mints **one ordinary task** and dispatches it
through the normal work pipeline.

Making it an ordinary task is the whole design. It inherits the entire existing
verification chain with no second oracle written for it: the review gate runs
`run_completion_gates`, so the build/test oracle reads its `CodeExecutionRecord`
rows and refuses an unverified or failing build, the completion-oracle peer
review fails closed without a distinct reviewer, and output policy, red team,
and vision all apply. A bespoke "integration checker" would have been a second,
weaker gate.

Three shape decisions carry weight:

| decision | why |
| --- | --- |
| forced `LEAF` (`WorkItem.leaf_required`) | splitting an assembly job hands the pieces back to separate agents, which is the state the stage exists to end |
| `plan_id` set, `plan_item_id` unset | it belongs to the initiative without implementing any plan item, so every derivation over items ignores it and it cannot distort the rollup that opened the stage |
| id derived from the plan id (`uuid5`) | idempotency with no "already started" flag to drift from reality: a re-fired stage finds the existing row and stops |

It declares two expected artifacts, the integrated runnable deliverable and the
end-to-end test run over it, which arms the zero-artifact guard: an integration
that produced neither terminates `NO_OP -> FAILED` rather than reading as
finished. It runs one stakes level above the plan's highest item, because
assembly is the first point the whole thing runs and the last point before
delivery. Its acceptance criteria are the objective's own.

Outcome, read from the task's persisted status on the next rollup recompute:

- `COMPLETED` (so the gate passed it): plan `INTEGRATING -> EVALUATING`;
- `FAILED` / `REJECTED` / `CANCELLED`: the replan trigger fires with
  `INTEGRATION_FAILED`;
- anything else: still working, nothing happens.

## EVALUATE

`engine/initiative/evaluate.py` runs a bounded session in which the accountable
lead judges the delivered whole against the objective's success criteria. It is
modelled on the SHIP retrospective session: detached, wall-clock bounded, turn
and cost capped, never raising into the rollup.

The verdict is per-criterion and evidenced (`EvaluationReport`), not a single
thumbs-up. Two invariants make it load-bearing:

- **every criterion is judged exactly once.** A submission that drops one, or
  invents one the objective does not have, is rejected back into the session
  for correction rather than accepted with the unanswered criteria treated as
  met;
- **`PARTIAL` is not a pass.** An initiative is delivered when the objective is
  met, not mostly met.

The session's tools are read-only by design (workspace read and list). A session
that could change what it is judging could turn its own failing verdict into a
passing one.

### Fail closed

No report, an unresolvable lead, no provider, a timeout, an objective with no
criteria: every one of those leaves the plan sitting at `EVALUATING` with a
warning on each recompute. Nothing completes an initiative on a missing verdict.

This is the deliberate inverse of the red-team gate's policy, and it is the
point of the whole change: an evaluation that did not happen is not a pass. An
operator resolves a parked plan by replanning or cancelling.

## Auto-replan

`replan_initiative` was fully built and reachable only from a human
`POST /plans/{id}/replan`. Nothing noticed when an initiative ran out of ways to
advance, so a stalled plan simply hung until someone looked.
`engine/initiative/replan_trigger.py` is that missing driver. The successor
lands in `PENDING_REVIEW`, which is the human gate the product already has: the
organisation gets itself unstuck, the operator still decides.

### What counts as stalled

A **shape, not a duration**: the plan has outstanding work and none of it can
move without a new decision. There is no threshold to tune and no timer, and the
derivation is exact the moment the last live item dies.

Three cases are deliberately excluded, each because replanning would destroy
something:

| not a stall | why |
| --- | --- |
| work still moving (`CREATED` through `IN_REVIEW`) | it is simply in flight |
| a human wait (`AWAITING_INPUT`, `AUTH_REQUIRED`) | the org is waiting on the operator; a replan would discard the question rather than answer it |
| an item whose task row does not exist yet | dispatch writes the plan's `EXECUTING` status *before* it creates the task rows, so treating this as dead would replan every initiative during its own dispatch window |

The two tail stages produce verdicts no derivation over items can see (every
item is `COMPLETED` when integration fails), so `INTEGRATION_FAILED` and
`EVALUATION_UNMET` are carried in by the stage and re-confirmed by the plan
still sitting in the stage that produced them.

### Loop safety

`Plan.replan_generation` counts how many times an initiative has replanned
itself. A successor opened automatically carries its predecessor's generation
plus one; a human replan resets it to zero, because a human decision is not a
runaway. Past `engine.auto_replan_max_generations` the initiative is **parked,
not failed**: it keeps its status and its stall stays visible for an operator.

Every fire re-reads the plan and re-confirms the stall before doing anything,
which is what makes a redelivered rollup event harmless: the first replan
supersedes the plan, and every later attempt reads a superseded plan and stops.

## Degraded boots

Each collaborator degrades independently, and none of them degrades into a
completion:

| unwired | behaviour |
| --- | --- |
| integrate stage (no work pipeline) | plan parks at `INTEGRATING`, WARNING per recompute |
| evaluate stage (no provider) | plan parks at `EVALUATING`, WARNING per recompute |
| replan trigger (no coordinator) | a stalled plan stays stalled and visible |

Parking is the honest outcome: an initiative whose pieces were never assembled
has not delivered, and an initiative nobody scored has not been shown to meet
its objective. The operator's remedy is one the product already has: replan
(legal from both tail stages) or cancel.

Wiring is best-effort and re-runnable. The rollup is wired as soon as
persistence and the task engine exist, which is before setup has configured a
provider, so a first boot legitimately produces a rollup with no tail; re-running
the wiring after setup attaches whatever now resolves, without re-registering the
observer, so the tail comes online with no restart.

## Settings

Every key below is read when the tail fires, so an edit applies to the next
run of it rather than needing a restart.

| key | default | purpose |
| --- | --- | --- |
| `engine.auto_replan_enabled` | true | master switch for the stall trigger |
| `engine.auto_replan_max_generations` | 2 | generation cap stopping a runaway chain |
| `engine.auto_replan_timeout_seconds` | 600 | ceiling on one re-decomposition |
| `engine.integration_stage_timeout_seconds` | 1800 | ceiling on minting + dispatching the assembly job |
| `engine.evaluation_session_max_turns` | 10 | evaluate session turn cap |
| `engine.evaluation_session_cost_ceiling` | 1.0 | evaluate session spend ceiling |
| `engine.evaluation_session_timeout_seconds` | 300 | evaluate wall-clock ceiling |

## Enforcement

`scripts/check_verified_completion_paths.py` (pre-push + CI) holds up the three
structures this page depends on: the forbidden edges stay forbidden and
`COMPLETED` keeps exactly one predecessor in both machines; only the evaluate
stage writes `PlanStatus.COMPLETED`; and both plan-shaped models still enforce
that a WORK unit declares a deliverable. Opt out per-line with
`# lint-allow: verified-completion -- <reason>`.
