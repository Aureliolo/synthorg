# Workstream Extension

What happens when a workstream finishes the tree it was given without
covering the objective it was given for.

This page is the authority on the extension mechanism. [Initiative
Tail](initiative-tail.md) owns the tail machinery it fires alongside
(replan trigger, stall escalation, the integrate/evaluate stages); [Recursive
Decomposition](recursive-decomposition.md) owns `PlanItem.unsplit_reason`,
the signal this mechanism reads; [Plan Review](plan-review.md) owns the
pre-dispatch reading of the same field.

!!! warning "Not the build loop's Slice planning stage"

    [The Build Loop](build-loop.md) names a future, unbuilt "slice planning"
    stage that plans the next slice of a tree *before* building it, with its
    own trunk merge, gate profile and finding channel. This mechanism ships
    today, acts strictly *after* a leaf has already been dispatched and
    completed, and reuses none of that design. The two names collided during
    review, so this page's mechanism is called an "extension" throughout,
    with "graft" kept as the verb for the mechanical append operation.

## The problem

Approval-time decomposition plans a workstream's whole tree up front, and a
depth, session, or turn-budget backstop can stop a unit's split before it is
genuinely atomic (see [Recursive Decomposition](recursive-decomposition.md)'s
"The reported condition"). That unit still dispatches and can still
complete: the backstop bounded how far the split went, not whether the work
that shipped covers what the unit claimed. `PlanItem.unsplit_reason` records
exactly this gap, and until this mechanism existed, nothing read it back:
a workstream whose last such leaf completed was read as fully delivered, and
the two remedies available (an operator raising the backstop's bound, or a
reviewer narrowing the objective) both require a human to have noticed the
flag on the item's card before dispatch. A workstream that finishes
unattended never gets either.

## Concept

**Workstream**: not a new entity, exactly `PlanTree.workstreams`, the plan's
top-level (parentless) `PlanItem`s.

**Extension**: a further decomposition of one already-dispatched,
already-completed leaf's remaining claimed scope, grafted as new children
under that leaf. The leaf becomes a container; its own task, already
`COMPLETED`, is left untouched.

The trigger question is narrower than "is the workstream's objective met":
a workstream needs an extension once its entire known subtree is
terminal (`workstream_needs_extension`, in
`engine/initiative/extension_state.py`) and at least one of its completed
leaves still carries `unsplit_reason` (`leaf_needs_extension`). Both are
pure derivations over already-persisted facts, so neither is a judgement
call and neither needs a judged check.

## Data model

No new persisted entity, no new table, no migration, no new `PlanStatus`.
`Plan.items` gains new rows per extension, appended under a version guard
(`_append_extension` in `engine/initiative/extension_graft.py`); `PlanTree`,
`item_is_done`, `collect_item_progress`, and the rollup all keep reading
`Plan.items` unchanged. A plan mid-extension is simply `EXECUTING`: the
newly-grafted items are not yet done, so `derive_plan_status`'s own
all-done check naturally holds the plan there, with no branch added to that
function.

A workstream's extension count is derived, never stored:
`workstream_extension_generation` counts the workstream's descendants that
are both a container and still carry `unsplit_reason`. The field is never
cleared once written (see `PlanItem.unsplit_reason`'s own docstring), which
is exactly what makes this count derivable at all: clearing it on graft
would erase the history the count depends on. The graft's own trailing
assembly child (below) never carries the field, so it cannot inflate the
count.

## Trigger flow

Fired from `ProjectRollupService.recompute()`, in `rollup_stages.py`'s
`extensions_hold`/`drive_extensions`, before `derive_plan_status` runs on the
same pass: that derivation promotes a plan to `INTEGRATING` the moment
every known item is done, with no workstream-level distinction. If
a workstream needs an extension, grafting adds new, not-yet-terminal items
first, so the same-pass `derive_plan_status` call correctly reads the plan
as not yet all-done.

For each of a workstream's leaves still needing an extension, the loop asks
whether this organisation may graft one unasked
(`ReplanTriggerService.consider_extension`, delegating to
`extension_graft.consider_extension`):

1. `ALREADY_RUNNING`: an extension is already in flight for this workstream
   (see in-flight keying below); this ask collapses into it.
2. `DISABLED`: the automatic-authority switch (`engine.auto_extension_enabled`)
   is off.
3. `BUDGET_EXHAUSTED`: the per-workstream generation cap
   (`engine.auto_extension_max_generations`) is spent.
4. `ASKED`: the deterministic autonomy gate applies (below); a decision is
   parked and nothing is grafted automatically.
5. `GRAFTED`: none of the above; a detached graft starts.
6. `UNAVAILABLE`: the in-flight tracker could not start the detached work at
   this moment.

`GRAFTED`, `ALREADY_RUNNING`, and `UNAVAILABLE`
(`EXTENSION_IN_PROGRESS_DISPOSITIONS`) hold the plan at `EXECUTING` this
pass. `ASKED` holds the plan only if an escalation service is attached, by
parking one decision (below). `DISABLED` and `BUDGET_EXHAUSTED` end this
leaf's road: no automatic route remains for it, and the workstream is left
as delivered, with this leaf's remaining scope surfaced later at the judged
`EVALUATING` gate, the same place any other unmet objective surfaces,
rather than by a second parking mechanism.

### Two doors

`consider_extension` and `grant_extension` are the same two-door pattern
`ReplanTriggerService.consider`/`grant` already use for a stall: the
organisation acting unasked applies every guard above, while a person's own
decision (approving a parked ask, or an operator granting one directly)
bypasses all three guards, since the switch, the cap, and the autonomy gate
all bound what the org does unasked, and somebody has just asked. Only
`ALREADY_RUNNING` still applies to `grant_extension`: granting one while
another is in flight for the same workstream would be a second, uncoordinated
dispatch rather than an answer to anything.

An `APPROVED` decision is re-applied through `grant_extension` on every
rollup pass it is still seen (`rollup_stages.py`'s `drive_extensions`),
because unlike a stall's grant (which supersedes the whole plan and ends the
loop for it), granting an extension leaves the plan at `EXECUTING` and the
same leaf recurs in `workstream_needs_extension`'s own answer until it either
gains children or is refused outright.

### In-flight keying is per workstream, not per leaf

Both doors key their in-flight tracking on
`f"extension:{plan.id}:{workstream.id}"` (`_in_flight_key`), not the leaf.
The generation cap is read from the workstream's whole subtree, so two of
its oversized leaves considered on the same rollup pass would otherwise both
read the same pre-graft generation count and both pass the cap, landing one
extension too many between them. Keying on the workstream serialises them:
the second leaf's ask reads `ALREADY_RUNNING` and defers to a later pass, by
which time the first extension has either landed (changing the count) or
failed (freeing the key).

This also settles what would otherwise be a retry-budget question:
`_append_extension` retries a version conflict once (two attempts total).
That budget is sized for exactly one concurrent writer per workstream, which
the per-workstream keying enforces; it is not sized for N-way concurrency
across sibling leaves, because that concurrency no longer exists once two
leaves under one workstream cannot extend at the same time.

## Deterministic gate

A new `ActionType.PLAN_EXTEND_WORKSTREAM = "plan:extend_workstream"`
(`security/autonomy/enums.py`) names "graft another extension onto a live,
still-executing workstream" as its own gated action, deliberately excluded
from `WORKTREE_CONFINED_ACTION_TYPES`: unlike an ordinary code or docs
change, this widens what the organisation may build unasked past what was
originally approved, so a bare `"code"`/`"docs"` grant under a built-in
autonomy preset never auto-approves it.

`consider_extension` decides `GRAFTED` vs. `ASKED` by explicit membership
only (`auto_approved`, in `extension_autonomy.py`): the action type must
appear in the plan's effective `auto_approve_actions`, never inferred from
its mere absence from `human_approval_actions` (that inference would
auto-graft under every preset that never names the action type at all,
which is every preset but `LOCKED` and `FULL` as shipped). An autonomy that
could not be resolved (no resolver wired, or the project row unreadable)
fails closed to the gate applying.

## The assembly item

Grafting a leaf children makes it a container, but that leaf's own task is
already `COMPLETED` from its original dispatch, and `item_progress.py` maps
exactly one task per plan item by a derived id, so the leaf's existing task
cannot be rewritten into the assembly job a real container would need.
`_extension_assembly_item` (in `extension_graft.py`) solves this by minting
one more ordinary child alongside the extension's own new items: a
fresh-`uuid4()`-id `PlanItem`, parented under the leaf, depending on every
newly-grafted sibling so it runs last, carrying no children of its own so it
reads as an ordinary work item to `assembly_of`/`task_from_item`. It is
built through the same `build_assembly`/`assembly_title` machinery any other
container's assembly uses, addressed at the leaf's own tree position. It
never carries `unsplit_reason`, so it neither reads as a leaf still awaiting
its own extension nor inflates the generation count above.

## Escalation

`ExtensionEscalationService` (`extension_escalation.py`) is parallel to
`StallEscalationService`, not a reuse of it: an extension decision and a
stall decision resolve differently (one grafts more work onto a live
workstream; the other replans or ends the whole initiative) and must not
share one idempotency key. It raises exactly one `initiative:extension_ask`
decision per leaf (re-checking its own idempotency inside `escalate()`
before writing, since the rollup's own pre-check and this write are not
atomic), and exposes `open_decisions(plan)`, one store scan per rollup pass
reusable across every leaf found needing a check, plus the module-level
`decision_for(decisions, leaf)` filter, rather than one scan per leaf.

Answering the decision routes through
`api/controllers/_approval_initiative_extension.py::try_initiative_extension_resume`,
parallel to the stall controller and claimed before the review-gate flow in
`signal_resume_intent`'s dispatch chain for the same reason: an unclaimed
decision carrying the objective task's id would otherwise reach the review
gate and be read as a plain completion review.

## Subsystem and settings

`CapabilityId.INITIATIVE_EXTENSION_ESCALATION` attaches onto the already-wired
rollup once `PROJECT_ROLLUP_SERVICE`, `PERSISTENCE`, and `APPROVAL_STORE` are
all up, independently of the sibling stall-escalation subsystem: the two
raise different decisions under different idempotency keys, so one being
wired says nothing about the other, and each is read by its own liveness
probe (see [Initiative Tail](initiative-tail.md)). Absent, the mechanism does
not hold the plan at `EXECUTING` for an ask it cannot park; the gap is
surfaced later at the judged `EVALUATING` gate instead.

| key | default | purpose |
| --- | --- | --- |
| `coordination.jit_extension_planning_enabled` | `false` | master switch, unvalidated by any live round, unlike recursion itself |
| `engine.auto_extension_enabled` | `true` | automatic-authority switch, on the same shape `auto_replan_enabled` ships |
| `engine.auto_extension_max_generations` | `2` | per-workstream generation cap, mirroring `auto_replan_max_generations` |

The master switch is deliberately the only one of the three defaulting off.
Flipping it is the one conscious decision that arms unattended grafting;
the other two then behave exactly as their `auto_replan_*` counterparts
already do, rather than requiring a second gate an operator must also
remember to flip.

## Out of scope

- The judged, escalate-only "is this extension a real departure from the
  forecast" check. The gate above is deterministic only.
- Retiring `INTEGRATING`/`EVALUATING` or the whole-tree final assembly.
  Untouched by this mechanism; a separate concern needing the trunk
  invariant the build loop's own aspirational design depends on.
- Reconnaissance, the finding channel, and the build loop's own slice
  planning and gate-ratchet state machine (see the warning above).
