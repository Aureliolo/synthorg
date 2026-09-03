# Recursive decomposition and the depth experiment

One agent in a loop cannot hold a whole application. That is the constraint the
mechanism on this page answers: the binding limit on building software with
agents is decomposition quality rather than agent supply, so work is split
until each unit is one agent's worth, the leaves are built concurrently, each
in its own workspace, and each level is assembled from the level below it. The
fan-out is not there to make a run finish sooner.

A decomposition is therefore a tree rather than a list. `DecompositionService`
assesses every subtask it plans and decomposes again any that is more than one
agent's work, against a child context one level deeper. This page covers three
things: the recursion mechanism, the durable form the tree takes so an operator
can review it and a dispatcher can run it, and the harness that measures what
happens to the work as the tree deepens.

!!! info "Scope"

    [The Build Loop](build-loop.md) is authoritative on how work is planned and
    assembled. This page owns the recursion mechanism, the durable tree, and the
    depth experiment's evidence.

    The build loop replaces whole-tree planning with **slice planning**: the
    next set of concurrently-runnable units is planned against the current
    trunk, with everything the previous slice learned. Depth is emergent there
    rather than configured, so the depth cap this page treats as the
    experiment's independent variable is a property of the harness, not a knob
    the designed loop exposes. It also replaces bottom-up assembly with
    continuous integration, which removes the fan-in cost the curves below are
    measuring.

    The evidence on this page therefore remains valid as a measurement of the
    loop as built, and is not a measurement of the loop as designed.

Recursion ships ON, and the sweep below is weaker evidence than it looks: its
depth-1 cells scored 0 of 42 in both arms against 36 of 42 at depth 2, but that
recording judged a merge on whether a path the PLANNER named before the tree
existed had changed, and most merges at every cap failed that test. The pilot
therefore cannot separate what depth did from what the verdict did, and the
replication is what will. What still stands from it is narrower and does not
depend on the ranking: the harness planned and assembled a tree of 58 units
with no replanning at all.

That last point is the thing to keep in view. A static hundred-item tree
planned in one pass, at the moment of maximum ignorance, is waterfall applied
recursively. The bounds below are runaway backstops rather than controllers:
the size signal is the controller, and its feedback loop closes without an
operator. Just-in-time replanning would be the execution-side half of that, and
it is not built.

## The question

Two bodies of published work bear on deep decomposition and they do not meet.
ARIES measured aggregation deterioration as work is decomposed and recombined,
with no verification at the joins. The Six Sigma multi-agent verification model
argues that gating each join arrests exactly that decay, and measures nothing.
No paper connects them.

What hangs on the answer is whether decomposition capacity is a property of one
LEVEL or of the whole tree: whether verifying at every merge holds the survival
rate flat as recursion deepens, so depth buys scale, or whether it does not, so
the limit binds however deep the tree goes. That is what the sweep below exists
to measure, and it is not settled. No figure for that limit is stated anywhere
on this page in either direction, because none has been established.

## Depth is counted in levels, on both sides

`max_depth` is a count of PLANNING LEVELS, not a count of edges.
`RecursionBudget.has_room` asks whether `current_depth + 1 < max_depth`, so a
node plans at `current_depth` 0 through `max_depth - 1`: a cap of three means
three levels of planning, the root plus two below it.

The harness reports the same unit, and the conversion is worth naming because
the raw signal disagrees with it: `max_depth_reached` is the deepest node's
zero-based INDEX, so a run that used its whole cap of three carries a 2, which
beside `depth_cap=3` reads as a tree that stopped a level short.
`evals/recursion_depth/tree.py::achieved_levels` owns the conversion and is the
only place it happens. A leaf's own `depth` stays an index and is never
converted: nothing bins on it, since the curve buckets each run on the depth its
TREE reached.

For an experiment whose independent variable is depth this decides how the
x-axis is labelled, so it is stated once here: **a cap of N fully used reports
N.** The S2 review's "depth four" is `max_depth=4`.

## The recursion point

`DecompositionResult` is a tree node. It carries its own `depth` and the
`children` of whichever of its subtasks were split again; both default to the
flat shape, so a tree that never recursed reads exactly like a flat plan.

Three derived views read the tree, and which one a caller wants is decided by
what it is asking. `all_tasks` and `all_subtasks` answer what the plan CONTAINS
(routing, the rollup, the park that needs a unit's declared role).
`dispatch_subtasks` answers what waits on what, and is the DAG a dispatcher
runs; a container appears in it, because a container that split is dispatched
as [its own assembly task](#a-container-is-its-own-assembly-task) rather than
as the work below it, which would do that work twice. `leaf_tasks` names the
tasks nothing below them replaced, which is the leaf count the tree reports.

`_do_decompose` is the recursion point. After the DAG is validated and the
tasks exist, each subtask is assessed, and one that is oversized with budget
left is decomposed again against a child context carrying `current_depth + 1`.
That copy in `engine/decomposition/_recursion.py` is the only write of
`current_depth` in the tree.

Recursion is decomposed after the tasks are built rather than before, because a
child level decomposes the **task**, not the definition: it inherits the
project, the type and the delegation chain the same way any other dispatch
would.

Levels are decomposed sequentially rather than fanned out. Each child level is
itself a planning session against a provider, and a level of eight subtasks
fanning out at every level turns one decomposition into a burst nothing here
rate-limits.

## The size signal

`SubtaskAtomicityPolicy.assess` decides, from the subtask's own declaration and
with no extra LLM call, whether a unit is one agent's worth of work. Three
conditions, any of which makes it oversized:

| Condition | Setting | Ships at | Role |
|---|---|---|---|
| `len(expected_artifacts)` | `coordination.subtask_max_artifacts` | 10 | Loose guard |
| `len(acceptance_criteria)` | `coordination.subtask_max_criteria` | 10 | Loose guard |
| `len(satisfies)` | fixed at 1 | 1 | The controller |

The third is the operative one and it is not configurable: a unit advancing
several of the objective's own success criteria is several units, whatever its
artifact count says. It terminates on its own, which is what makes depth
emergent rather than a number somebody picked: a unit claiming one criterion
hands its children a vocabulary of exactly that one, so they can claim at most
it, and the tree stops once every unit advances exactly one thing. Depth
therefore tracks the objective's own criterion count: less if it needs less,
more if it needs more.

The induction rests on the criteria DESCENDING with the recursion. Re-read per
level instead, each level mints a fresh vocabulary out of what the level above
happened to write: a child task is built from its subtask's own
`acceptance_criteria`, which is the planner's per-item prose. A claim made below
the root then names nothing the objective ever stated, the claim count is
bounded by `subtask_max_criteria` rather than by what the parent held, and the
recursion-depth sweep dropped 143 claims it could attribute to nothing.

`DecompositionContext.objective_criteria` is what descends. It is stamped once
at the root from the objective's own criteria (`stamp_objective_criteria`,
beside the bounds resolve, so one tree is never planned against two
vocabularies) and narrowed by `child_context` to exactly the criteria the
parent unit claimed. Once stamped, `child_context` is its only writer, the same
way it is `current_depth`'s. The narrowed tuple carries the OBJECTIVE's spelling
rather than the claim's: matching is forgiving about case and spacing, and
handing the claim's text down instead would move the vocabulary one
normalisation step per level until a deep claim matched nothing at all.

The child task keeps its own `acceptance_criteria` as the local definition of
done, so below the root the planning message carries two lists and says which is
which: `Acceptance Criteria` is when THIS unit is finished, and
`Objective criteria to cover` is what the objective the whole tree serves is
still waiting for. An item's `satisfies` is copied out of the second. At the
root the two coincide and one list is rendered, under the heading the submit
tool's schema names. BOTH planners render it, because either can run: the
single-shot decomposer builds it in `build_task_message`, the agent-session one
in `planning_brief`, and a strategy whose schema names a heading its own prompt
omits is judged on a list it was never shown.

A level narrowed to NOTHING admits no claim at all rather than admitting every
claim. That case is reachable and not rare: a pure-support unit is judged
oversized on its artifact count with `satisfies` never entering the decision, so
it is recursed into, and letting its descendants claim freely put text into a
plan that the operator's own edit boundary then refused. Refusing there is what
keeps every level's vocabulary a subset of the root's.

The backstops below are still not redundant. A planner can under-tag, which
does not break the induction but flattens the tree (a unit claiming nothing is
atomic by this rule and is never split), and a level can still be refused for
reasons of its own. The depth and session backstops bound the walk, and the
last-level correction asks the planner to spend breadth first.

The first two are loose "obviously too large" guards rather than the
discriminator. At one artifact, an item that writes a module plus its test read
as oversized, which made artifact count (a proxy for how verbosely a particular
planner writes) the thing deciding the shape of every tree.

`subtask_max_artifacts` exists because `coordination.leaf_subtask_threshold`
was read by two consumers that want opposite values. It keeps the
objective-level `LeafThresholdRoutingPolicy`, where an objective declaring two
deliverables is a team's work; atomicity got its own key, where a subtask
declaring two is still one agent's.

The failure mode this creates is stated rather than hidden: an objective with
no acceptance criteria leaves `satisfies` empty everywhere and nothing splits.
That is why the two guards stay real rather than being opened to their maxima
the way the sweep arms them. In the product's own path an objective always
carries criteria (the charter interview produces them, and `evaluate.py` parks
a plan whose `objective_criteria` is empty), so this guards a hand-filed
objective rather than the normal case.

The signal is only asked about WORK items. A `DECISION` item is a choice among
its declared options rather than work to divide, and the policy reads only the
artifact, criterion and claim counts, so one declaring several acceptance
criteria would read as oversized and open a child planning session that plans
work nobody asked for.

## When a split is refused rather than made

While a level below is still available, an oversized unit is simply decomposed
again. That is the measured behaviour.

At the LAST permitted level there is nowhere to delegate to. Dispatching the
oversized unit whole behind a log line is what that costs when nobody asks: a
live run left twenty-one units carrying five to twelve objective criteria each
against a limit of one, and nothing told the plan it had guessed wrong.

The level is handed back instead. `DecompositionService` is the single owner of
"is there anywhere left to split into" and stamps the size signal onto the
context it plans the last level under (`_held_to_size`);
`_atomicity_gate.describe_unsplittable` then refuses the submitted plan at
parse time, on the same correction channel a graph violation already takes.
Both strategies answer it the way they already answer a malformed plan: the
single-shot loop re-prompts through `with_retry_context`, and the agent-session
submit tool hands the reason back as its tool result. The correction asks for
BREADTH: split these further at this level, since there is no level below.
Breadth spent where depth ran out, without an operator being asked.

`manual.py` is exempt by construction: `_split_oversized` early-outs on
`strategy.plans_any_task()`, and an operator-supplied plan has no session to
correct.

**The correction carries the width cap, and is withheld at exactly it.** A
correction that asks for breadth without naming how much room is left is asking
for the one thing the level can then be refused for: a level complying with it
produces one unit too many and `DecompositionSubtaskLimitError` fails the whole
tree. A live run did exactly that, widening to eleven against a limit of ten
precisely as instructed, where compliance was fatal, non-compliance was
rejected, and no amount of retrying could resolve it. So
`describe_unsplittable` takes `width_limit` from the same budget the strategy
enforces after the session, states it in the correction, and asks a level that
is already OVER the cap to merge or drop rather than to widen. A level sitting
exactly AT the cap is the one silent case: there is no depth below it and no
width beside it, so it draws no correction at all and its units dispatch under
the depth backstop instead. Equality rather than "at or above", because a level
over the cap can still be saved by merging and is exactly the one that needs to
hear the cap named.

Only when the retries are spent does the condition reach the plan, as
`PlanItem.unsplit_reason`, and it gets there through a typed error rather than
through the generic one. A child planning session that exhausts its retries on
the size correction raises `DecompositionUnsplittableError`; the level that
ASKED for that session catches exactly that class, files
`PLANNER_DECLINED` on the unit and dispatches it whole. Its own plan is valid
and its sibling units are dispatchable, so discarding the tree above it would
throw away every level already paid for to report one unit's size.

The type is what keeps that from becoming a swallow. It is one member of a
declared set of child failures a level may absorb, and everything outside that
set propagates; the set and its reasoning are in
[the backstops](#the-backstops-and-what-a-bind-reports) below.

## The backstops, and what a bind reports

Five bounds, none of them a target. Depth and width are runaway guards: what
decides a split is the size signal above, so a small objective stops on its own
well short of either.

| Setting | Bounds | Ships at |
|---|---|---|
| `coordination.decomposition_max_depth` | levels of planning | 5 |
| `coordination.decomposition_max_subtasks` | units one level may produce | 10 |
| `coordination.decomposition_tree_max_sessions` | planning sessions per tree | 40 |
| `coordination.decomposition_timeout_seconds` | one planning session | 600s |
| `coordination.decomposition_tree_timeout_seconds` | one whole `decompose_task` call | 14400s |

`DecompositionContext.max_depth` and `max_subtasks` are `int | None`, where
`None` means "not declared, read the operator's setting". They are resolved
ONCE at the root by `decompose_task` and stamped into the context it recurses
with, so one tree is never planned under two budgets. That is an ordered
precedence ladder with one resolver (caller declaration, then the operator's
setting, then the definition default), not two authorities: the recursion-depth
sweep keeps passing explicit values and keeps control, while a production
caller that passes nothing gets the operator's setting rather than a hardcoded
number.

The session budget is the one that stops GRACEFULLY, which is why it exists
beside the wall-clock ceilings rather than instead of one. Past it no further
child session is opened, the tree returns what it has, and the units it could
not split say so.

**A bound on ONE NODE ends one node.** A per-session ceiling exists so a level
waiting on a provider that never answers cannot hold the tree. Letting its
breach propagate does the opposite: it hands every node in a deep tree an
independent chance to destroy every other node's work. A live run spent 39
planning sessions and 1h 48m, reached `sessions_remaining=2` of 40, and
discarded every level because session 39 ran 599.7s against the 600s ceiling
while it was in the last-level correction loop.

So a child whose session ends on its own terms is ABSORBED by the level that
asked for it, exactly as a child the planner could not split is: that level
already holds a valid plan, the unit dispatches carrying the reason, and the
tree survives. `_child_failure.py` is where the set is declared rather than
counted, keyed on the error type because that is what carries the remedy: the
wall-clock ceiling (`DecompositionTimeoutError`), the turn budget
(`DecompositionTurnBudgetError`), the token budget
(`DecompositionSessionBudgetError`), a session that stopped making progress
(`DecompositionStagnationError`), and a planner that declined to divide the
unit (`DecompositionUnsplittableError`). Everything else propagates: a
transport that keeps mangling replies is fixed at the provider, and filing it
as a note on one plan item hides an outage.

Absorbing those buys no unbounded time, because the two TREE-scoped bounds are
untouched: the session budget still caps how many such nodes one tree can pay
for, and the whole-tree ceiling still fires as a bare `TimeoutError` that no
handler absorbs, because that bound covers every level and no level holds a
plan that outlived it. At the ROOT there is no plan above to carry the unit, so
the same breach fails the decomposition and says so.

The whole-tree ceiling is therefore the one that raises and discards every
level already paid for, which is why it is set well above a real tree rather
than at it. It is not a multiple of the per-session one and cannot be derived
from the depth cap: sessions scale with the NODE COUNT, which is the branching
factor to the power of the depth, so any multiple of the per-session number is
a guess that kills a legitimate deep tree. It is a catastrophic backstop rather
than an operating bound, because the session budget is what bounds a tree's
cost. Two of the four callers are request handlers.

### The reported condition

A unit that reached the plan still oversized carries `PlanItem.unsplit_reason`,
naming the rule that fired, both numbers, and which bound stopped the split.
The phrasings are kept apart because the remedies differ, and each is a line in
`atomicity.py` rather than a count stated here: the depth backstop and the tree
session budget each want their own bound raised; a child session that ran out
on its own terms names WHICH of its three bounds it hit
(`SESSION_CEILING_BACKSTOP` wall clock, `TURN_BUDGET_BACKSTOP` turns,
`SESSION_BUDGET_BACKSTOP` tokens); `STAGNATION_BACKSTOP` says the session
stopped making progress, which is a defect to fix rather than a bound to
raise; and `PLANNER_DECLINED` wants a narrower objective because no bound was
reached at all. It is written by the projection from what the service recorded, and
it is deliberately ABSENT from `PlanItemPayload`: an operator editing the item
has just revised it, and a note about the version they replaced describes
nothing.

It is read where the two remedies (raise the bound, narrow the objective) are
the reader's: as a flag in the plan's attention panel and on the item's own
card, and in the stakeholder panel's brief beside the item, where a reviewer
raising `OVERSIZED_SCOPE` has the machine's own evidence in front of it.

A third remedy needs no reviewer to detect, once a leaf carrying this field
has already been dispatched and completed: [Workstream
Extension](workstream-extension.md) reads exactly this field, post hoc, to
decide whether the workstream that dispatched it is actually finished or only
finished the tree it was given, and grafts a further decomposition onto the
leaf rather than raising a bound or narrowing an objective nobody has looked
at yet. Whether that graft needs a reviewer to approve is a separate
question the deterministic autonomy ladder answers per case, which can still
park the decision for a human.

Those units still dispatch. Refusing the plan would throw away every level
already paid for and block work whose leaves may well execute; the honest
answer is to run it and say so.

### How they are read

All five are read live, per decomposition, so an operator raising one applies
to the next call rather than the next restart. A read that fails falls back to
the definition's own default only for the two things the setting itself can be
wrong about: the key is unregistered, or its stored value is not the right
type. Anything else, a settings store that is down above all, propagates for
the wall-clock ceilings, because those are re-read once per node and swallowing
a transient failure would run an arbitrary share of a tree under a bound nobody
chose.

A bound nobody can read is never a licence to spend: every fallback lands on a
real number, and the depth, width, and session backstops mirror their
definitions in code (`context.py`, `_recursion.py`, `_ceilings.py`) so a
harness running with no settings backend at all is still bounded.

### What the sweep arms, and why it differs from the product defaults

The recursion-depth sweep writes its settings through the real service, so what
it measured is only interpretable against what it armed
(`evals/recursion_depth/tree.py::arm_recursion`, logged as
`evals.recursion_depth.settings_armed` at the start of every run):

| Setting | Product default | The sweep arms | Why |
|---|---|---|---|
| `recursive_decomposition_enabled` | on | on, or off for the control arm | The variable under test |
| `subtask_max_artifacts` | 10 | its declared maximum | Opened all the way so the requirement floor is the one rule that decides a split |
| `subtask_max_criteria` | 10 | its declared maximum | The same manipulation, on the other threshold |
| `decomposition_timeout_seconds` | 600s | 2400s | Sized for a model that answers directly; every model worth sweeping reasons first, and losing an arm to a timing margin destroys the comparison rather than slowing it |
| `decomposition_tree_timeout_seconds` | 14400s | its declared maximum | A sweep is not a request handler, and the default is sized for the ones that are |
| `decomposition_tree_max_sessions` | 40 | its declared maximum | Unlike every other bound here, this one does not kill a cell: it stops the split and returns a PARTIAL tree, which the sweep would then record as the depth it asked for rather than the depth it got. A depth-4 cell is above a hundred planning nodes on this page's own branching model, so leaving it at the default silently flattens the independent variable at exactly the depths worth paying for |
| `decomposition_max_retries` | 5 | 6 | A cell that never plans destroys its pairing rather than costing a data point |
| `providers.retry_max_attempts` | 3 | its declared maximum | Widens the ladder between the hosted gateway and the real upstream provider, where a momentary blip otherwise terminates a session thirty turns in and nothing re-enters that conversation. It does NOT widen the harness driver's own ladder, which takes its budget from the company config so a recorded artefact stays reproducible from the config it names |

The sweep declares its own `max_depth` per cell, which is the variable it
sweeps, so `decomposition_max_depth` is never armed: the caller's declaration
wins over the setting by construction.

The five armed at a declared maximum read it off the definition rather than
copying the number, so a product bound that changes carries the sweep with it
instead of surfacing as a write the settings service refuses partway through a
paid run.

Sampling is the one part of the treatment that is **not** armed through
settings, because it is not a setting: temperature, `top_p`, reasoning depth
and the per-response ceiling belong to the bound model, so each is declared on
the manifest's own `ModelPair` and reaches the roster as the agent's
`ModelConfig` (`evals/recursion_depth/staffing.py`). Per pair rather than once
for the matrix, because the two models a cross-family matrix binds are
published with different values on different dials, and which dial a model even
honours differs by family: one may expose graded effort and ignore sampling
while thinking, another expose no effort parameter at all. A single figure for
both is therefore guaranteed wrong for one of them.

Declaring it there rather than on the command line is what puts it inside
`manifest_sha256`, which `matrix_identity` pins in the journal header, so a
resume against a changed treatment is refused rather than mixing two into one
curve. The recorded `Provenance` carries both pairs read back off the
**dispatched identity** (`ModelPair.of`), not off the manifest, so the report
publishes the binding that actually ran; a dial reading `unset` there is one
the binding did not state, which per-call resolution may still have answered
for, rather than proof that no request carried a value.

The `decomposition_max_retries` row is a margin rather than a manipulation, and
what makes it worth reading twice is how narrow the margin turns out to be. The
setting counts RETRIES, and the first ask is not one, so a value of N allows N+1
attempts. A subtree in a live run was refused four times and converged on the
fifth: at any default below four it would have failed a couple of attempts short
of the plan it went on to produce, and a live round records exactly that outcome
as "the replan then exhausted its decomposition retries". At the shipped five,
the sweep's six is a cushion rather than the difference between planning and
not.

Arming the per-session ceiling ALONE is worse than arming neither, and is the
mistake this table exists to prevent: it raises what one session may spend while
the whole-tree ceiling keeps a default sized for a request handler, leaving an
outer bound that cannot admit even two of the sessions the inner one allows.
Every tree killed that way has already paid for the levels it planned, and the
sweep files it as an unavailable cell, which reads as "the planner could not
decompose this" rather than "the harness could not finish a tree it was paying
for".

A timeout is also the one planning failure the sweep does not retry
(`DecompositionTimeoutError`): the ceiling is unchanged on the next attempt, so
a retry reaches the same place having paid it twice, and at these ceilings the
second attempt is measured in hours.

No published system has a signal like this at all, so it is deliberately a
small measurable rule rather than an elaborate one.
Its limitation is stated rather than hidden: the assessment is made from the
**planner's declaration**, so a planner that under-declares produces a unit
nothing splits. That is why the experiment's results carry a caveat saying so
on their face.

## The tree, made durable

`PlanItem.parent_id` is the whole of it: the item this one was split out of, or
`None` when nothing contains it. `plans.items` is a JSON column, so the field
needed no DDL and no migration; the claim is asserted against both real
backends in `tests/conformance/persistence/test_plan_repository.py` rather than
reasoned about.

**Containment is not order.** `parent_id` says what an item belongs to and
never when it runs, which `PlanItem.dependencies` alone decides. The one place
the two meet is a rule about what a dependency may SAY: it must name a unit at
the same level, which `DecompositionPlan` already required before the tree
existed. A cross-subtree need is stated between the containers, which the tree
already expresses.

`core/plan_tree.py::PlanTree` is the single owner of every derived question
(which items are workstreams, what hangs off an item, how deep it sits, what
order assembles bottom-up), so "this item was split" cannot drift from "this
item has children" the way a declared flag would.
`core/plan_tree_validation.py` owns the invariants only the whole set can
answer: a parent resolves, the parent graph reaches a workstream, and a
`DECISION` is never a parent (it is chosen rather than decomposed, and dispatch
strips it, so its children would be orphaned).

### Telling a working tree from a hung one

The tree is persisted ONCE, at the end, which is correct and leaves the plan
reading `PLANNING` with zero items for the whole run. A live run sat at zero
for 54 minutes under a page that promised "items appear as they are written",
and the only way to tell a working decomposition from a hung one was the
backend log. Everything needed to answer it was in the session ledger already,
and the ledger knew it only in memory.

`plans.decomposition_progress` carries the answer to the page. It is a
**snapshot, not a log**: overwritten each time the tree reaches a new node,
because the question is "where is this now" and the run's own history is the
event stream's job. It names the sessions spent against the sessions allowed
(so the number reads as progress rather than a bare count), the deepest level
reached, the units written so far, and when the snapshot was taken. That last
field is what distinguishes working from stalled, which is the whole question
an operator has while the item count reads zero. `NULL` means nothing has been
reported yet, which is a different claim from a zero snapshot.

The write is deliberately unlike every other plan write. It is ONE conditional
statement, `UPDATE ... WHERE parent_task_id = ? AND status = 'planning'`,
because the status is a WRITE condition rather than a read one: a plan can be
failed under a live decomposition, and a whole-row write from a snapshot taken
before that would revive it and discard the reason. It asserts no version and
bumps none, because the decomposition ends by claiming its shell at the version
it started from; a progress line that moved the version would fail the very
write it exists to describe. It leaves `updated_at` alone for the same reason:
this describes a run, it does not revise a plan.

The engine reaches it through `DecompositionProgressReporter`, a one-method
seam wired at the worker assembly. The service holds no repository, so a
harness plans without persistence at zero cost, and publishing is allowed to
fail by contract (`_progress_publish.py`): a reporter that raises is logged and
dropped, because losing the progress line costs an operator a refresh while
losing the tree costs the run.

### Both directions of the projection

`items_from_decomposition` walks `children`, so every level reaches the plan. A
child node's `plan.parent_task_id` IS the id of the subtask it was split out
of, so the parent link is read off the tree rather than derived a second way.
`decomposition_from_plan` rebuilds the nested `DecompositionResult` from the
persisted links, which is what makes an operator-edited tree the one that
actually builds, and gives a nested item's task its CONTAINER's task as
`parent_task_id` rather than the objective's.

### How containment reaches the wave gate

`DecompositionResult.dispatch_subtasks` is a derived view in which a container's
`dependencies` are augmented with its children's ids. The wave builder
reconstructs its `DependencyGraph` from that view, so a container lands in a
strictly later topological level than the subtree it assembles while
independent subtrees stay in the same wave. `gate_wave` then already asks
exactly the right question, and a container parked `dependency_failed` names
the child that died, which reads honestly.

The augmentation exists ONLY inside that view. The persisted
`PlanItem.dependencies`, the persisted `Task.dependencies` and the planner's own
`SubtaskDefinition` are untouched, asserted by test, so `dependencies` keeps
sole ownership of declared order.

### A container is its own assembly task

An item with children is not dispatched as work; that would do the work twice.
`engine/assembly.build_assembly` is the single owner of that: it gives a
container an assembly brief over its own children, its declared artifacts plus
that subtree's own namespaced evidence paths, and stakes one rung above the
highest of what it assembles. Its `plan_item_id` is set, so `tasks_by_item`,
`collect_item_progress`, `item_is_done` and the rollup work unchanged, and it
runs through routing, waves and the review gate like any other item.

BOTH paths reach that owner, because there are two and they are not the same
call. `decomposition_from_plan` rebuilds a tree from the durable plan (the
approve-a-reviewed-plan route), and `DecompositionService` builds one in memory
(`coordinate()` with no precomputed plan). A container is an assembly on one
and its own oversized work description on the other is not a smaller version of
the same bug: it is the double execution, on whichever route a given deployment
takes.

It reaches BOTH ends of one decision too. Routing admits candidates against
`SubtaskDefinition.stakes` and dispatch judges `Task.stakes`, so the escalation
is applied to each from the one verdict; escalating only the task routes a
container to an agent the dispatch then refuses.

The evidence namespace is keyed on the container's whole ADDRESS in the tree
(`.synthorg/integration/<slug>/<slug>/`, one segment per level), not on its
position among its own siblings. A sibling position repeats across the tree, so
two cousins both sitting first under different parents, both titled "Setup",
resolve to one directory and overwrite each other's report: the collision the
namespacing exists to prevent, rather than a corner of it. The chain of
positions is unique by construction, and each segment still carries the
sanitised title so an operator can read the path.

That is what turns one wide fan-in at the top into a sequence of narrow ones.
Whether the width was ever the binding constraint is open: the pilot's own
table has the deeper, wider-fanning trees scoring better, so narrow assembly is
argued for here on the grounds that a merge brief stays readable, not on a
measured collapse. The root keeps today's `INTEGRATING` tail stage, and its
brief now names the plan's WORKSTREAMS rather than every leaf in the tree.

## The experiment

`evals/recursion_depth/` sweeps the depth cap and emits one chart with three
panels on one depth axis: the fraction of the specification a merged tree
satisfies, the fraction of the delivered leaves' own claims the merge kept,
and the headline, tokens per solved requirement with a 95% bootstrap
interval over each bucket's runs. The headline is what ranks the arms: a
published comparison of three harnesses (arXiv 2607.22585) measured a
forty-fold cost separation while every pairwise pass-rate interval but the
largest included zero, so a loop can be cheaper by an order of magnitude at a
pass rate no interval separates, and a report ranking on satisfaction alone
ranks two things it cannot tell apart. The interval is a seeded percentile bootstrap
(`evals/recursion_depth/efficiency.py`), seeded from the runs themselves so a
re-score reproduces it; a bucket under three runs reports the point with no
interval rather than a fabricated one, and two arms whose intervals overlap
at a depth are named in the caveats as indistinguishable there. The depth axis is not
the one the question asks for, and [The metric](#the-metric) below says why it
stands in.

Beside the score, and never folded into it, every measured cell carries a
**liveness** verdict on the deliverable the specification names
(`evals/recursion_depth/liveness.py`). The oracle says which requirements a
tree satisfies; it does not say whether `python -m sqlcsv` runs, and the two
can come apart: an agent can satisfy a hidden oracle while the requested
artefact is dead (arXiv 2606.28430), and the gap between visible and held-out
verdicts grows about 28 points per tenfold increase in code size (arXiv
2605.21384), which is the direction a depth sweep pushes. The spec's
`requirements.yaml` declares what must run (`liveness: modules` and
`entry_points`); each module is imported and each entry point executed with
`-I` in a throwaway container holding the tree ALONE, so nothing the probe
runs can read an expectation, and the verdict is `live`, `dead` (with what
died) or `not_probeable` (the spec declares nothing to run). A cell scoring
well while its deliverable is dead is named in the caveats, because that is
the published failure mode the probe exists to catch.

The committed matrix records caps 1 to 4 at five repetitions each, one arm,
because `MINIMUM_REPETITIONS` refuses to load a matrix asking for fewer: the
same comparison found that below five trials a pass-rate difference is
indistinguishable from the run-to-run spread. The harness still supports two
arms, and the first recording used both; what changed and why is in
[The gate](#the-gate).

Run `make recursion-depth` to print the matrix and the bill without spending
anything, and `make recursion-depth-record` to measure for real.

### Before a matrix is paid for

A recording refuses to start without a passing **wire-level smoke** for its
own manifest digest (`scripts/record_recursion_depth.py --smoke`, read by
`--record` from `<out-dir>/smoke/wiring.json`). A 200 response, a valid
manifest and a green unit test are all compatible with a treatment being
absent from the engine: the corpus this harness replaced measured an engine
wired at 8 of the 51 points a deployment supplies, for eight recordings, and
nothing at any layer could tell. The smoke runs one cell at the shallowest
cap and reads each treatment off EVIDENCE rather than configuration
(`evals/recursion_depth/wire_check.py`): the engine's own wiring summary
(`AgentEngine.wiring`, the same facts its creation event logs, plus the tool
surface the invoker was built with), the live settings the manifest was armed
into, the cell ledger (cached-prefix tokens), the recorded request bodies
(the reasoning depth that actually reached the provider), the review gate the
host built (the completion-oracle peer review is attached only once a
coordination pair is published, and a runtime built without one reviews
nothing), and the product's own verdict on a finished leaf, read back off the
host rather than off the harness. Each becomes a finding with what was
expected and what was seen; a treatment whose evidence cannot be read is
`unverified`, which is neither a pass nor a failure and is said in those
words. A leaf that ends at its turn cap is never offered for review, so a
cell in which no leaf finished leaves the leaf-review finding unverified
rather than failed. The findings travel in the report under `wiring`, so a
published artefact states its own wiring rather than asserting it, and a
recording made before the smoke existed says "not measured" there.

### What one run does

1. **Plan.** The shipped owner-run planning session decomposes the
   specification down to this run's cap, through the real
   `DecompositionService` with the settings written through the real settings
   service. One session per node that plans.
2. **Fix the contract.** One forced-leaf session per cell, between the plan and
   the first unit, writing module layout, cross-module signatures and one
   FAILING test per requirement. Its tree, not the committed seed, is what
   every later unit and merge is recreated from, so the agreement is already in
   each checkout and nothing has to be handed to anybody. Governed by
   `contract_stage` in the manifest and by `--contract-stage` /
   `--no-contract-stage`; with it off, step 3 seeds from the committed README
   as it always did.

   It exists because the seed alone fixes nothing: measured across the three
   corpus cells recorded without it, 11 of 14, 11 of 12, and 12 of 13 of the
   modules more than one child wrote disagreed on their exports, against 0 of
   21 in a cell recorded with it. Re-derivable from the kept trees with
   `scripts/report_interface_divergence.py`.
3. **Build every leaf.** One agent owns a unit end to end, its own tests
   included, in a workspace recreated from the contract's tree (or from the
   committed seed where no contract ran). A leaf **delivered** when it changed
   something it declared and its own tests pass in its own tree (below).

   Under a contract, "its own tests" narrows to the tests naming the
   requirements that unit CLAIMS, because the seeded checkout carries a failing
   test for every requirement and each unit is told to leave the others
   failing. Grading a unit on the whole suite there does not misjudge
   occasionally: it marks every unit undelivered whatever it built.
4. **Assemble every node, deepest first.** The children are copied under
   `.children/<slug>/` and the deliverable is the tree at the workspace root.
   The merging agent is told it may change a child's interface and is asked to
   record each time it does.
5. **Judge, or spend the same budget without judging.** See below.
6. **Grade.** The held-out oracle runs against the root's assembled tree.

Preflight settles what it can before any of this: provider coverage, a
reachable daemon, a one-token completion on both pairs, and, once the host has
resolved it, that the sandbox image the run will need actually exists. That
last one runs after the boot because unless `--sandbox-image` names one the
reference comes from the running instance's own settings resolver; it still
beats the first session, which is what matters, since planning runs entirely
through the gateway and a cell that can never be graded would otherwise buy a
whole plan first.

### The metric

Two curves, on one axis, from one grading. They answer different questions and
the pair coming apart is the finding; `evals/recursion_depth/score.py` owns
both.

**SPECIFICATION.** After the root merge the held-out oracle runs over the whole
specification, and:

```text
        | requirements the merged tree satisfies |
    y = ----------------------------------------
        | requirements the specification defines |
```

The denominator is fixed at 42 for every cell, so every run produces a point,
including where a cell's leaves all failed. `DepthPoint.fraction` sums both
operands per `(depth, arm)` bucket. What it does not say is where the work came
from.

**SURVIVAL.** The question the programme was built around:

```text
        | requirements DELIVERED leaves claimed that the merged tree satisfies |
    y = ----------------------------------------------------------------------
        |          requirements DELIVERED leaves claimed                       |
```

`SurvivalPoint.fraction`, binned on the same axis so the two read off one
chart. Delivery rather than standalone correctness gates the denominator: at
depth a unit is one function and nothing above it exists yet, so a leaf's own
tree usually cannot run the spec oracle at all and requiring a standalone pass
would empty the denominator exactly where the curve is most interesting. This
denominator IS leaf work, so it can empty, and an empty one is an ABSENT point
rather than a zero: nothing was measured there, and a zero says everything was
lost.

**How a claim reaches a requirement.** Leaf work is claimed through
`SubtaskDefinition.satisfies`, which carries criterion TEXT rather than
requirement ids, because text is what the planner is given and copies back. The
root objective is filed with one criterion per requirement, `"R03: A decimal
column reads as a float"`, and the criteria descend with the recursion (see
[The size signal](#the-size-signal)), so a claim at any depth still names one of
them and `recursion_depth/claims.py` resolves it on the id token it carries.
The criterion carries the requirement's title as well as its id because at
depth the specification prose is gone: the child task describes the unit, not
the spec, and an id alone is not something a planner can allocate against.

The map is checked once, on the tree the planner produced and BEFORE the first
leaf session opens (`tree.claimed_requirements`, called from
`_build_tree_units`). A claim naming no requirement raises there, so the cell is
recorded unavailable having spent its planning sessions rather than its whole
leaf budget. That is the backstop; the product refuses such a claim at the
boundary the planner writes it, where the session can still correct it.

**What the pair buys.** A tree scoring well because the merging agent rebuilt
the work and one scoring well because its leaves' work survived are the same
number on the specification curve and different numbers on the survival one.
Every emitted artefact carries `METRIC_CAVEAT` stating both, because the chart
and the JSON travel without this page.

**What the repetitions buy.** Both curves POOL a bucket's runs into one
fraction. That is the right shape for a rate over work and it cannot say
whether a low point is one bad draw or a real drop, which is the entire reason
a cap is recorded five times. So `DepthSpread` reports each bucket's range and
its middle run per metric, and `depth_curve.md` also lists every cell on its
own row. The middle is the LOW median, so it is always a figure some run
actually recorded rather than one describing neither of two. The absent-point
rule applies per RUN there: a run whose delivered leaves claimed nothing has no
survival rate and is left out of the range rather than folded in as a zero,
which would report a collapse nobody measured.

**A third column, which is not a curve.** `shared_modules` and
`diverged_modules` on each cell record how many module paths more than one unit
wrote, and how many of those the units disagreed on. It is reported beside the
score rather than folded into it, because it measures the CAUSE the contract
stage addresses rather than the outcome the oracle grades, and the two move for
different reasons. Counted per module path and only where more than one unit
wrote it: a module one unit owns cannot disagree with anybody, and folding those
in buries the reading under the many files each cell writes once. Agreement is
the module's public surface, never its bytes, since two units are SUPPOSED to
write different bodies for a module they share.
`evals/recursion_depth/divergence.py` owns it and
`scripts/report_interface_divergence.py` re-derives it from any kept recording
without a provider call.

### What delivery is decided by, and what it is not

Delivery is a question about the AGENT's work, so it is decided by what the run
produced: the session took a turn, it changed something the unit declared, and
the unit's own tests pass in its own tree. The middle condition goes through the
product's own `ArtifactPresence.delivered_nothing_since`, against a probe taken
before the session, so a path the recreated seed already provided is not
credited to the run that received it.

It is deliberately NOT decided by the planner's declared list being complete,
and that is a correction rather than a preference. The harness asked whether ANY
declared path was missing, which is the inverse of the rule stated in the module
that owns it ("the 'none, not some' rule is this module's to state once"), and
it made delivery turn on a plan-time guess written per node at whatever
granularity the planner chose: the same output was a delivery under a parent's
two-entry list and a non-delivery under the leaf's four-entry one. One live unit
wrote its module, a 31-test suite and ran it, and was booked at 598,585 tokens
as no delivery because an empty `tests/__init__.py` was absent, which resolves
without one and which its own passing suite proved it did not need. At a
delivered rate near a half that term dominated the curve rather than sitting in
its tail.

The declared list is still recorded, as `UnitRecord.missing_declared_paths`,
because a planner over-declaring is worth seeing. It holds the paths the planner
named that the finished tree does not contain, which is a mismatch between the
two rather than a fact about either on its own. It is a diagnostic and nothing
more: nothing reads it to decide `produced`, and delivery is settled by the
tree.

The direction is stated because the field's previous name, `undeclared_paths`,
read the other way and was cited backwards on this very page. One recorded unit
holds `('sqlcsv/csv_loader.py', 'tests/test_csv_loader.py')` there. Under the
old name that is a unit that wrote its module and its test suite, which is the
most convincing evidence of success available; it is the list of what that unit
FAILED to write. No type, test or docstring caught it, because each was
correct. What catches a name pointing the wrong way is reading it against a
concrete value, which is why the value is here rather than a description of it.

### Achieved depth, not the cap

The cap is what a run was allowed; the planner decides what it uses, and a
planner that stops splitting at three produces identical trees at caps four,
five, and six. Binning on the cap would make those look like three measured
points and a flat right half would read as "gating holds at depth" when it
means "nothing went there".

So the primary curve bins each leaf on **its own level**, the cap curve is
reported beside it, and the achieved-depth histogram is split per arm, because
an arm plans its own trees and two arms compared at a depth only one of them
reached is two experiments on one axis.

### The gate

Every merge in the committed matrix calls `CompletionOracleGateService.evaluate`
unchanged. The harness supplies the engine the reviewer runs on and nothing
else: selection, the exclusion of the executor, the narrowed review session, the
fail-closed escalation and the verdict's attribution all stay the product's. A
rejection feeds its findings into a repair attempt.

What the harness does change is which tree the reviewer is pointed at: it gets
a **detached copy**, and the graded tree is the original. The gate prompt
requires a disconfirming command, so the reviewer holds the terminal tool
whatever its file tools allow, and a reviewer able to touch the tree it judges
could repair the work under review, which would credit gating for work the gate
itself did.

An escalation is recorded, never resolved. There is no human in a sweep, so the
merge stands and the parked count travels with the chart: a line resting on
unresolved escalations is a different claim from one resting on verdicts.

**Why one arm.** The harness still ships the `ungated` arm, which spends the
identical attempt budget with nobody independent in the loop: a self-review by
the agent that just did the merge, whose output no verdict is taken from. It is
the honest control for crediting the gate with **independence** rather than
effort, and the first recording ran both.

It is not in the committed matrix, and the reason is what the first recording
could and could not support. The arm difference there was 36 against 35 and 36
against 33 at one repetition each, with the arms on **different trees** (58
leaves against 43, then 38 against 30): inside the noise, indistinguishable
from zero and equally impossible to rule out as substantial. Fixing that needs
either a shared tree, which leaves the two arms' spend impossible to compare
while spend is the check on the whole result, or enough repetitions to
average the tree draw
out, which doubles a bill that already runs to hundreds of sessions per cell.

Gating is not the binding question. Replication is, and depth 4 is where ARIES
puts the blow-up, so halving the arms is what pays for both. It also dissolves
the paired-tree confound rather than requiring a fix for it: with one arm there
is no arm comparison left to confound. The gated arm survives because the
shipped product gates every merge, so the curve describes the product's own
behaviour rather than a configuration nobody runs, and because it was not the
dearer arm in this pilot (3.05 merge attempts against 6.00, since an ungated
merge has no verdict to stop on and always burns its whole budget). **That gap
was measured under a harness bug** (see below: a park used to end the repair
loop early, the same way an approval does), so a gated merge that parks now
also burns its whole budget rather than stopping after one round -- the cost
comparison is not assumed to hold under the fixed harness and needs
re-measuring, not carried forward as a settled fact.

Leaf-level verification is untouched either way, so what the recorded curve
measures is depth under the product's own aggregation.

### The judge is checked, not assumed

A judge sharing the executor's `(provider, model)` pair biases straight toward
the null, which matters whether or not an ungated arm is being compared against:
the recorded curve is what a gated merge produced, and a gate whose reviewer is
the executor is not a gate. The manifest declares an
independence class and the loader refuses a manifest whose pairs do not match
what it claims; identical pairs are refused outright. Under `same_family`
every artifact carries the caveat on its face.

That claim is checked against a declared `family`, never against the provider.
Self-preference attaches to the organisation that trained a model, and the
connection reaching it is a separate fact: an aggregating provider serves many
families through one endpoint, so two models behind it are as decorrelated as
their families are, while two connections to one vendor are not decorrelated at
all. Deriving family from the provider would refuse the first case and wave the
second through, which is backwards in both directions. A `cross_family` claim
whose pairs name one family, or name none, is refused.

Each pair also declares its capability rung, and family is declared beside it
for the same reason. The capability registry grades a pair from a catalogue that
knows nothing about a placeholder id, and selection refuses an ungraded pair
outright, so a roster built from a manifest that did not say would leave every
review unstaffed and the sweep would record escalations rather than verdicts. A
placeholder id has no discoverable family either.

Because the manifest ships placeholders, a company config decides which real
models answer them, which makes family a fact with two owners: the manifest's
copy is what the claim is checked against, and the config's copy names what
runs. A config aliasing both placeholders onto one organisation therefore
satisfies every check in the manifest and still produces a correlated judge, so
the recorder compares the two before the host boots. What it compares is the
RELATION and never the names: a vendor-agnostic placeholder cannot equal a real
organisation's name, so testing for that would refuse every real recording,
while the claim those placeholders make (that the two pairs differ) survives
aliasing intact. A config declaring no family is not a disagreement; it is the
config not saying, which leaves the manifest the only claim.

### The oracle is held out

`evals/recursion_depth/spec/sqlcsv/` defines a SQL-over-CSV query CLI in 42
numbered requirements. `requirements.yaml` maps each to the behavioural pytest
that decides it, and those node ids never leave `evals/recursion_depth/oracle.py`:
they reach no brief, no workspace, and no prompt. An agent told which test
decides a requirement builds to the test, and every leaf and merge brief says so
in as many words, which is the largest single measured countermeasure against
reward hacking and costs one prompt change.

The specification is proved satisfiable by a reference implementation under
`tests/evals_spine/recursion_depth/reference_tree/`, which passes all 42, and
the oracle is proved discriminating by an empty tree, which fails all 42.

### What the sweep costs, and what bounds it

A depth sweep's session count is a product of branching factors the manifest
cannot predict, and the cost of being wrong about it is spend rather than a
wrong answer. So plan mode prints what a **full tree** costs at a declared
branching, the manifest carries a hard `max_sessions` ceiling, and hitting it
stops the sweep
and reports what was measured with a caveat saying so. `--depths` stages the
bill: record the shallow end, read the curve forming, then pay for the deep end.
Those stages are CUMULATIVE, and this is the one thing about them worth stating
twice: the report holds exactly the caps the invocation planned, because
`run_sweep` replays a journalled cell only when the narrowed matrix still asks
for it. A final stage naming only the deepest cap therefore emits a chart
missing every cap already paid for. A replayed cell costs nothing, so each stage
names every cap recorded so far and adds `--resume`.
`--max-sessions` lowers the ceiling, and it is folded into the manifest rather
than applied to the run, so the figure the plan prints is the one the run
enforces: a ceiling applied downstream of the plan shows the manifest's own
number beside the flag that was meant to lower it, at the one moment the number
is being relied on.

`--repetitions` is the third lever and the one that reaches the bill, because a
cap costs its branching to the POWER of its depth: one repetition fewer at the
deepest cap buys back more time than any other single change, and the shallow
end is nearly free either way. It takes `CAP:COUNT` pairs and changes only the
caps named. Per run rather than by editing the file, because the committed
counts are the experimental DESIGN (five at every cap, the floor
`MINIMUM_REPETITIONS` refuses to load a matrix below, so per-depth spread is
reportable everywhere rather than only where the transition is expected), and an
operator trading one of them for a schedule should not leave the next reader
inheriting a quota window as if it were an intended design. The floor binds the
override as well as the file: the NARROWED matrix is re-checked against it
after the counts are folded in, so `--repetitions` can raise a cap or hold it
at five and is refused below that, because a cap dropped to one repetition
reports a range of one draw, which is the thing the design exists to avoid.
All three levers are folded into the
manifest OBJECT and none touches the manifest FILE, which is what the journal's
identity pins, so none of them turns a resumable matrix into a foreign one. A
COMMIT does, because the identity pins that too, and that is the constraint that
actually governs a staged recording: fix everything before the first stage, and
carry the tree unchanged until the last one.

That reasoning holds only for a SCHEDULE lever, and the distinction is load
-bearing. `--max-sessions` and `--repetitions` decide how much of the matrix
runs; `--contract-stage` and `--leaf-reasoning-effort` decide what running it
MEANS. A treatment that changed the loop while leaving the file alone would
leave two arms with byte-identical headers, and the identity check would then
accept a resume of one inside the other's directory and splice two loops into
one curve. So `Provenance.loop` carries the treatments a run actually resolved
(`LoopTreatments`) alongside the file digest, and `Provenance.sandbox_image`
carries the image it resolved, both inside `matrix_identity`.

`--leaf-concurrency` is the only lever that changes wall clock rather than the
bill: sibling leaves at one level have no dependency on each other, so they
build together. Merges never overlap, and the cell loop stays sequential, which
is what keeps one cost ledger per cell readable.

### Re-emitting a report without paying again

`--rescore` rebuilds the whole report from the journal alone. It reads no
manifest, contacts no provider and spends nothing: the cells, the provenance and
both curves are all recoverable from what the recording already wrote. It exists
because a scoring or rendering defect found after a multi-hour run is otherwise
only fixable by paying for the run again, and because a report produced by a
scratch script is reproducible by nobody, which is most of what the provenance
block is for.

A re-score REBUILDS every caveat it can derive and CARRIES only the declared
`RUN_STATE_CAVEATS`, the three that record how one run went (a session ceiling,
a quota refusal, a same-family judge) and that the journal does not hold.
Deriving the rest is what gives an old recording the current release's wording;
carrying them instead left a re-scored report holding two wordings of the same
caveat side by side.

`--repair-spend-from` takes the recorder's own log and rebuilds the token
column from it, for a recording whose sessions shared one process-wide cost
sink. That sink is swapped per session, so concurrent leaves could journal zero
while a neighbour absorbed their records; the log is written one line per CALL,
which no swap can scramble. Attribution is by interval rather than by task id,
because the root task id is derived from the specification and repeats across
every cell: a unit's spend is what banked against its id since that id was last
journalled. The plan row is cut off explicitly, since planning dispatches under
the root id that the root merge later reuses. A repair that places no call at
all is refused rather than reported, because the caveat it would add is a
provenance claim.

The figure is derived from the TREE each cap admits, and it has to be, because
the arithmetic is unforgiving in exactly one direction. A projection that counts
one session per cell plus its merge attempts, and then adds "one per leaf and
per node on top of that", leaves the entire tree out: for the recorded matrix
that shape prints 42 against a real cost of roughly 158 sessions PER CELL, so a
ceiling chosen from it is about four times too small. A run launched at a
ceiling of 30 planned an 85-leaf tree, built six units, and stopped with **zero
cells measured**, which is the whole failure mode `max_sessions` exists to make
survivable and was instead the thing that fired.

`RecursionDepthManifest.projected_sessions` states the model instead: at a
declared `projected_branching` of `b`, a cap of `d` holds `b ** d` leaves and
`(b ** d - 1) / (b - 1)` nodes that planned, each of which also assembles, and
an assembly is two sessions (the merge and its review, in every arm). The
branching is declared in the manifest and PRINTED beside the figure it produces,
because a model whose input is hidden reads as a measurement, and it is rounded
DOWN from what a real tree showed (85 leaves over 25 planning nodes at cap 3
implies about 4.4).

That figure is a scenario, not a bound in either direction, and reading it as
one is how an operator gets surprised. `depth_cap` is a MAXIMUM the planner need
not spend, so a tree that stops shallow costs less than this says; and the
declared branching does not constrain the planner either, so a tree that
branches wider costs more. What it answers is the question worth sizing a
ceiling against, since the run that uses its whole cap is the expensive one.
`max_sessions` is what makes being wrong in either direction survivable: set it
too low and the sweep stops early with a caveat, which is the outcome this
whole section exists to keep survivable.

#### Sizing a ceiling and starting a cell are different questions

The ceiling books sessions AFTER they run, so on its own it can only stop a
sweep that has already overrun. A cell entered without the budget to finish it
spends everything left, records no `achieved_depth` and enters no curve: the
measurement is lost either way and the spend goes with it. `_refused_on_budget`
is what makes that recoverable, declining to START such a cell so the sweep can
be resumed against a raised ceiling with every finished cell replayed free.

It cannot use the projection to decide that, and the reason is the tree's shape.
`b ** d` assumes UNIFORM branching, and these trees branch wide at the top and
narrow below: the recorded cap-3 tree split 7 ways at the root, 4.6 ways at
level 1 and 3.5 ways at level 2. So the widest factor any recorded cell shows is
the ROOT's, and a forecast that substituted it (7, from a cap-1 cell holding 7
leaves over one level) answered **3,601** sessions for a cap-4 cell whose real
cost is near 300. Used as a refusal threshold that number refuses the deepest
cell of every sweep, which is the one the whole matrix is paid for.

So the manifest declares both, and each answers one question:

| Field | Answers | Read by |
|---|---|---|
| `projected_branching` | what a FULL tree costs at each cap, the expensive scenario | the plan print, and through it whoever sizes `max_sessions` |
| `expected_sessions_per_cell` | what ONE cell at each cap is expected to actually cost | `estimate_sessions`, which refuses a cell the remaining budget cannot finish |

`estimate_sessions` prefers a MEASUREMENT of the same cap over the declaration,
because the matrix repeats caps: once one cap-3 cell has run, what a cap-3 cell
costs is known rather than modelled, and the costliest such run is taken. The
declared figures still err HIGH, since refusing a cell that would have fit costs
one measurement while entering one that does not fit costs that measurement and
the spend; what changed is that the margin is a figure an operator sized from
measurement rather than an artefact of the wrong tree shape. Both are printed by
`make recursion-depth`, beside the ceiling, and beside each other.

Once a journal exists the manifest is frozen, so this figure is chosen once. The
journal header pins the manifest digest along with the commit, the spec, both
pairs, and whether the tree was dirty, and a resume against any of those changed
is refused rather than mixing two matrices into one curve. That dirty flag
deliberately excludes the recorder's OWN output directory
(`evals/harness/provenance.py::_dirty_argv`): the default one is tracked because
the artifact is committed, so without the exclusion a stage that finished would
dirty the tree with its own report and refuse the next resume, forfeiting every
cell it had just paid for. A change to the code still flips it and still refuses
the resume, which is the point: a fix changes the system under measurement. `--max-sessions` is the only lever a resume has, and it
works precisely because it is folded into the manifest object without touching
the file the digest is taken over. Editing `manifest.yaml` to run a cheaper
matrix forfeits the planning already paid for and means starting a new
`--out-dir`; `--resume --help` says so.

A unit is bounded twice, by `unit_cost_ceiling` and by `unit_token_ceiling`, and
the second is not redundancy. A flat-rate connection attributes 0.0 to every
call, so its cost ceiling can never fire and a runaway unit would be held by
nothing but its turn cap. Tokens are counted on every provider, so the plan
states the token bound as the one that holds without the reader first knowing
how they are billed.

Those bounds are per ROLE and scaled, not one flat number for every session.
`session_limits_for` sizes each role from a base the manifest declares plus
what that particular session is being asked to do, under a cap:

| role | base | scaled by | cap |
|---|---|---|---|
| leaf | `unit_token_ceiling` | requirements claimed, at `unit_token_per_claim` | `unit_token_cap` |
| merge | `merge_token_base` | pieces to assemble, at `merge_token_per_piece` | `merge_token_cap` |
| review | `review_token_base` | the same fan-in | `review_token_cap` |
| contract | `contract_token_ceiling` | nothing: one session per cell | itself |

Flat was the earlier shape and it is what starved the corpus: a leaf claiming
eighteen requirements got what a leaf claiming two got, and 58% of leaves
stopped on their ceiling rather than finishing. Every base and cap pair is
checked at manifest load, because a cap below its own base makes `min(base,
cap)` size every session of that role to the smaller number, which is
indistinguishable at runtime from the matrix legitimately needing less.

Every cap is repeated, which the first recording did not do: it ran one cell
per point and so could report no spread anywhere. Spread is only reportable
where there is a population, and a matrix that repeats only the caps it expects
to be interesting cannot say whether the flat ones are flat or merely too
thinly sampled to tell.

Repetitions are five at every cap, and the loader refuses fewer. The deep cells
are where the sessions are: a cap-1 cell is 14 sessions against a cap-3 cell's
135, and a cap-4 cell is projected near 300, so the earlier design tapered to
two at the deep end to fit the ceiling. Five is the sweep's own floor, adopted
from the protocol the harness comparison above was measured against, which
runs five trials per task; an independent bootstrap over that benchmark's
leaderboard found 24 of 25 adjacent rank pairs indistinguishable even at that
count (`evals/recursion_depth/results/harness-audit/README.md`, gap 5). The
comparison itself ran one trial per task and says nothing about repetition
counts; what it says is that pass rate alone separates almost nothing, which
is why a taper leaves exactly the cells the curve is about too thinly sampled
to read. What the taper was hiding is still worth stating: two draws bound a
range but do not give a median distinct from it, so caps 3 and 4 report a
spread that says how far apart two trees fell and not much more.

### Preflight

Provider coverage, a reachable Docker daemon, and a one-token completion
against each declared pair are all settled before the host boots. Each is a
property of the configuration or the machine, so none becomes truer once a
scratch database, a gateway and a container are standing, and each is otherwise
found by a unit failing mid-decomposition.

The misdiagnosis is what makes this load-bearing rather than merely faster. An
invalid credential surfaces as `decomposition.failed` and records the cell
unavailable with a `DecompositionError` reason, which names the wrong subsystem
entirely: the operator goes and reads the planner. Measured with a deliberately
invalid key, it took 56 seconds to get there, because the credential error was
retried by the driver, returned across the recorder's own gateway hop as a 502,
and retried again on the far side. A bad key fails identically every time, so
all of that is latency. The probe is also the warm-up, which matters because a
cold model load would otherwise land entirely on whichever cell is recorded
first, and that is depth 1: the flattest, cheapest point on the curve.

The sandbox image is checked separately and one step later, because unless
`--sandbox-image` names one the reference comes from the running instance's
own settings resolver and there is nothing to check until the host is up. It
still lands before the first session, which is the boundary that costs money:
a cell plans entirely through the gateway, so an image that does not resolve
would otherwise be discovered by the first container, with a plan already
bought (measured at 85,555 tokens). The daemon's own 404 is the only answer
read as "no such image": a 500 or a dropped socket is the daemon failing to
answer, is retried, and if it persists is reported as the daemon being
unavailable rather than sending an operator to rebuild an image that is fine.

### Two things to read the results against

Neither is a defect, and both change what a number means, so they belong beside
the number rather than in a run log nobody opens afterwards.

**Depth 3 is too shallow for this specification.** In the cap-3 run, 21 units hit
`depth_exhausted` at `current_depth=2` carrying 5 to 12 objective criteria each
against the atomicity limit of 1. At that cap the leaves are therefore NOT
atomic, and a delivery difference between the arms is partly a difference in how
oversized the units each arm was left holding were, rather than in what gating
the merges did.

**`docker.execute.failed` is not a health metric.** An agent probing what `int()`
accepts logs one per deliberate `ValueError`. That is the tool reporting a
non-zero exit from a command the agent meant to fail, which is correct
behaviour and useless as a signal: the count moves with how thoroughly an agent
explored, not with how badly a run went.

## What the wired engine measured

One cell, cap 1, the gated arm, recorded 2026-09-02 through the product's own
engine assembly with nothing missing (`evals/recursion_depth/results/wired-r0/`,
with the register answered question by question in its `README.md` and the
stop point in `docs/reference/harness-round-log.md`). Every recording above and
below this section ran 8 of the 51 collaborators a deployment passes; this is
the first that ran the loop the product ships, and the sweep was stopped at it
by operator decision, so it is one cell and it is read as one cell.

| what | measured |
|---|---|
| specification satisfied | 7 of 42, program LIVE |
| leaf-work survival | 1 of 8 claimed |
| tokens | 50.5M over 16 sessions, 4 h 37 min; the merge 49%, the leaves 36%, the reviews 10% |
| plan | 8 units in 11 turns; five of seven submissions refused because the array arrived as JSON text |
| contract | 15 modules, 46 pending tests, no `CONTRACT.md`, cut at its 60-turn cap |
| leaves | 8 of 8 at the 40-turn cap with the product's extensions zeroed by the harness; 2 graded delivered |
| merge | three attempts (80 turns, 80 turns, parked on its 5.5M-token ceiling), rejected three times by a roster reviewer: the first two on the same critical lines, the third on a missing test run and NULL ordering |
| shared modules diverged from the contract | 7 of 27 |

What it says about the six root causes, in the order the dossier named them:

- **RC1 (open-loop per unit).** Refuted as a property of the product: the
  post-execution path files every finished unit for review, and the roster
  reviewer judged the assembly three times in about a minute each with
  code-quoted findings. What stands is narrower: a unit that runs out of turns
  is never offered for review, and every leaf here did.
- **RC2 (isolation where agreement is required).** Stands, smaller: the
  contract closed most of the divergence (7 of 27 shared modules, against 11 of
  14 without one), and what remained was one unit's join signature and one
  unit's error taxonomy, both of which the assembly was told about and did not
  reconcile.
- **RC3 (the merge cannot do its job).** Stands, and now with the mechanism on
  the wire: the assembly was briefed with the reviewer's findings by name. One
  attempt read the named file four times, edited through `sed` and
  here-documents rather than the edit tool, and wrote nothing in 80 turns; the
  next fixed both lines at turn 50 of 68 and never ran the suite, so a
  `NameError` on every query's first line reached the kept tree past three
  read-only reviews. Repair rounds do not converge because the assembly
  neither acts promptly on what it is told nor verifies what it writes.
- **RC4 (nothing manages context).** Refuted as a cause at this pair: the
  largest request was 125K tokens against an 838K compaction threshold, so
  compaction correctly never fired; tool-output abbreviation fired ten times.
  Context is not what ran out.
- **RC5 (configuration validated, capability not).** Resolved by
  construction: the engine is the product's and the smoke reads each
  treatment off the wire. The smoke itself read three findings wrong on this
  cell and was corrected.
- **RC6 (unvalidated instruments).** Partly stands: the session-flow report
  read the planning session as idle because it records a non-streamed
  completion, and the wiring smoke misjudged three treatments; both are fixed
  and tested on this branch.

What decided the outcomes was not the loop's architecture. It was a 40-turn
cap with no extension, and a tool surface that refused a tokeniser as a
secret, raced concurrent edits of one file, refused missing parent
directories, hid parameter names behind a load round-trip, refused an array
sent as JSON text and told the agent how a test run is recorded only after
refusing it. All of that is fixed on the branch that carries this recording,
with tests. Underneath it the model dominates cost: 95 to 100 percent of
emitted text is hidden reasoning, and single turns ran to the 131K output
cap and sat silent for twenty minutes.

The design decision this round records is none of the five the issue offered.
The sweep was stopped before the repetitions that would have powered any of
them, because the same evidence that argues for fixing the plumbing argues
that the inner loop is not where this product's value is, which is now
#2916's question. The
INTEGRATE stage keeps its design; leaf repair rounds and tool-result
abbreviation are not motivated by this cell; the sweep design question is
moot until a next recording exists.

## What the sweep measured

The figures below are the PILOT: six cells, caps 1 to 3, one repetition each,
both arms, on an engine wired at 8 of 51 collaborators. They are superseded by
the wired-engine cell above as a description of the product's loop, and kept
because the process figures (attempts, amendments, parks) were measured over 35
merges rather than one.

`evals/recursion_depth/results/pilot/` holds it: `chart.svg`,
`depth_curve.json` and `depth_curve.md`, beside the `cells.jsonl` and
`progress.jsonl` the run journalled as it went, plus the `cells.raw.jsonl` the
first of those replaced when its per-call spend repair became the recording's
own ledger. 240 units across 482 agent sessions, no cell unavailable. It is
re-scorable in place with
`--rescore --out-dir evals/recursion_depth/results/pilot`.

| achieved depth | gated | ungated |
|---|---|---|
| 1 | 0.000 (0/42) | 0.000 (0/42) |
| 2 | 0.857 (36/42) | 0.786 (33/42) |
| 3 | 0.857 (36/42) | 0.833 (35/42) |

Every cap reached the depth it allowed, so achieved depth and cap agree
throughout and the histogram holds no surprises.

### The answer, and why it is not the answer the question expected

**Neither arm collapsed.** The gated line is flat from depth 2 to depth 3 while
its tree grew from 38 leaves to 58, and the ungated line ROSE, from 0.786 to
0.833. The question this experiment was built around assumed the ungated arm
decays, because that is what ARIES measured, and asked only whether gating
rescues it. There was nothing to rescue.

What that does NOT establish is whether the 11-to-25 coherent-unit ceiling is
per level or global. The tempting reading is that it is per level and depth
buys scale, since 58 units scored 86% and 58 is well past the band. But a
single tree at each cap, on a recording whose merge verdicts are known to be
unreliable, cannot carry that: it is one draw, and the contrast it would rest
on is the depth-1 row the section below shows cannot be read. The question is
open and the replication is what will answer it. What survives here is the
weaker and still useful fact that neither arm decayed with depth.

**The depth-1 zero is not a fan-in effect, and this recording cannot say what
it is.** The obvious reading, that seven units feeding one merge is an
integration too wide to survive, is contradicted by the table it is drawn from:
the deeper trees have more merges and more total fan-in, and they scored
better. What actually distinguishes depth 1 is that it has exactly ONE merge,
so that merge is a single point of failure with no second assembly to
compensate.

That merge was also judged by a rule since replaced. Both cap-1 merges record
`no assembly attempt changed anything the node declared`, and both record their
two declared paths as absent. The journal says nothing about what they wrote
INSTEAD, because the rule in force recorded only declarations: that is the
whole defect, and it is why the replacement reads the produced tree. So the row
may be an honest zero or a verdict written off a naming disagreement, and this
recording cannot tell them apart.

The rule was not confined to depth 1. Merges carrying that same verdict: 1 of 1
at cap 1, 6 of 7 at cap 2, and 12 of 19 (gated) and 12 of 16 (ungated) at cap
3, in both arms throughout. Every row of the table above rests on cells where
most assemblies were judged that way; the deeper rows are not clean readings
spoiled by a contaminated first row, they are the same defect not proving
fatal.

### What this run cannot support

**One repetition per cell, and each cell plans its own tree**, so treatment and
tree draw are confounded everywhere and there is not one controlled comparison
in the run.

The depth finding survives that on effect size alone, 36 requirements against a
largest arm difference of 3. It no longer draws support from cap 1 having failed
twice with the same logged mechanism: that mechanism is the declared-path rule
described above, so the repetition evidences a defect in how delivery was judged
rather than anything about depth. Call it supported, not proven.

**The arm difference does not survive it.** 36 against 35 at depth 3, with the
arms on different trees (58 leaves against 43), is inside the noise. The effect
of the gate on quality cannot be distinguished from zero here, and equally
cannot be ruled out as substantial.

What the arms DO differ in is process, measured over 35 merges rather than 2
cells: gated merges amend a child's interface 1.05 times each against 0.31, and
spend 3.05 attempts against 6.00, because an ungated merge has no verdict to
stop on and always burns its whole budget. Per recorded unit, plan row
included, the gated arm used 1.73 sessions against 2.58, and its cell cost 1.15M
tokens per leaf against 1.13M. The gate changes how the work converges; whether
it changes the result is unmeasured.

### This run's own attribution is not trustworthy

The figures below are the SPECIFICATION curve, which is the share of the
specification the merged tree satisfies rather than the share of leaf work
surviving the merge. This recording has a survival curve too, re-scored from
its own journal, but it reads `n/a` for the ungated arm at depths 2 and 3:
every claim that arm's planner made below the root named a criterion it had
invented one level up, so 143 of them attributed to nothing. That is the
per-level vocabulary defect [The metric](#the-metric) above describes, which
this recording ran under, so the figures here are read on the specification
curve throughout. A claim naming no requirement is refused where the planner
writes it and ends its cell before any leaf is paid for, so a later recording
carrying that caveat is reporting a regression rather than a known gap.

### Reading the verdicts, which are easy to read wrongly

**A `reject` does not mean the merge was discarded.** The gate is a repair loop:
findings feed the next attempt, the workspace is mutated in place, and the final
tree is used whatever the last verdict said. Both arms get the same attempt
budget; only the gated arm's repairs are informed.

**Parking short-circuited repair, against the gated arm, for this pilot.**
`run_merge` broke on `approved is True or parked` when this recording ran, so a
merge escalating with no human to decide got fewer rounds than a rejected one.
The gated arm parked 6 of 19 merges at depth 3 (against 1 of 7 at depth 2) and
the ungated arm parks never, so the arm credited with repair received less of
it as depth rose. This was a harness bug: a park no longer takes the
approval branch, so a parked merge now keeps repairing through its remaining
attempts like a rejected one does, and the report separates cells that never
reached a verdict from the judged curve rather than counting them as gated.
The figures above are this pilot's own, measured under the old behaviour.

**The reviewer pays a compliance tax the executor does not.** 31 verdict
submissions were refused for rejecting with an empty findings list, gated arms
only. Every retry recovered and no rejection landed without findings, so the
gate never degraded, but the cost falls entirely on the arm whose spend is being
compared.

### What was not recorded

Caps 4 to 6. ARIES puts the transition at 3 to 4, so the depth where the
literature expects a blow-up is exactly the one beyond this recording: the
chart's right end is absent rather than flat.

That is what the committed matrix now records: caps 1 to 4 at five repetitions
each, one arm. It answers the two things this recording could
not support, and takes the arm comparison off the table to pay for them.

### What the run keeps

Every request and response crossing the recorder's own gateway is written to a
JSONL transcript, one file per session, keyed on the same execution id the
ledger keys its spend to, so a transcript and the cost it produced name the
same session. They land under the run's work root beside the trees
`--keep-workspaces` leaves, and are read against them.

This is not diagnostics for its own sake. The chart answers what each cell
scored; the questions actually worth asking afterwards are why a merge was
rejected, what the reviewer said, and whether a repair round addressed the
finding or talked past it. None of that is recoverable once the run ends, and a
sweep costs too much to repeat because nobody kept the reasoning.

### Failures

Three outcomes, not two.

A missing provider, a dead gateway, a dead Docker daemon, or an oracle that
cannot run at all is true of every remaining run, so it stops the matrix rather
than being rediscovered once per cell at full retry cost. No report is written:
there is nothing to report on.

The provider account running out of quota is the second, and it is a property
of the ACCOUNT rather than of the cell that happened to ask last. Every
remaining cell would be refused within seconds and filed under a cell-shaped
reason, so the sweep stops there too, but it keeps what it paid for: the
triggering cell is recorded unavailable, a caveat naming quota is added, and
the report is emitted. One live sweep lost its whole remaining matrix in
sixteen seconds before this outcome existed, and each lost row blamed
decomposition.

Anything else records that one cell as unavailable **with its reason** and the
sweep continues: the report is always written, a cell that cost real money is
never dropped from it, and a run where nothing was measured is refused rather
than published as a curve of zeros.

### Where a sweep runs agent-authored code

Everywhere, in a container. The agents run in the sandbox image the CLI
verified, and so does everything that grades what they produced: each unit's own
suite, and the held-out oracle. Nothing a sweep executes runs on the host.

That is not belt-and-braces. Grading a tree means importing every `conftest.py`
and package `__init__.py` in it, so the process that grades is a process the
agent wrote. Running it on the host would give that code the network, the
operator's provider credentials, the bootstrap secrets the recording host puts
in its own environment, and the Docker socket, which is host root. The harness
already treats the agent's shell commands as untrusted and runs them at
`network=none`; running the artefacts of those same commands anywhere else would
have been the same code with the restrictions taken off.

Two consequences worth stating, because they are easy to conflate:

- **Containment is complete.** No network, no credentials, host unreachable,
  container thrown away.
- **The grade is still self-reported, and nothing can change that.** A tree that
  wants to claim its tests passed is running arbitrary code in the process doing
  the claiming. Reading the verdict from the machine-written XML report rather
  than the exit code stops the ORDINARY failures reading as passes (`os._exit(0)` in a
  `conftest.py`, a suite that collected nothing, a collection hook that
  deselected everything), which is what a model under pressure actually does.
  Deliberate forgery remains possible and is bounded elsewhere: forging only
  ever grows the survival DENOMINATOR, so it makes the measured result worse
  rather than better, and the numerator is decided by the held-out oracle, which
  never enters the tree and which the tree cannot write.

The oracle's container is built per grading from a scratch directory holding a
copy of the tree beside a copy of the oracle, and destroyed after. That is what
holds the oracle out from the workspaces: it exists only somewhere no agent runs,
rather than being kept from agents by nothing having copied it.

Inside that container the two are unavoidably adjacent. pytest has to read the
assertions and the delivered program has to be executable, and there is one
filesystem, so a delivery spawned with its working directory in `tree/` is one
`..` from `oracle/`. The suite therefore deletes its own expectations once
collection has imported them. That happens before any test body runs, which is
before the delivered program is ever spawned.

What may remain is an allowlist rather than a set of patterns to sweep:
`conftest.py`, `__init__.py` and `data/`. That distinction is load-bearing. The
first version removed `test_*.py` and left `__pycache__` behind, where the same
queries and expected rows were readable out of `co_consts`, and the test written
against the same predicate agreed that the directory was clean. Nothing compiled
is staged now, the sweep is keyed on what stays, the suite re-checks before every
spawn and the harness re-checks afterwards and refuses the measurement outright.
The adjacency is enforced, not prevented by construction, and it is worth saying
so in those words.

## Related

- [Coordination and resilience](coordination.md) for the wave dispatcher the
  production tail drives a plan with.
- [Verification and quality](verification-quality.md) for the completion-oracle
  gate this experiment treats as its independent variable.
- [Inner-loop A/B recording](../research/inner-loop-ab-recording.md) for the
  measurement the shared recording spine was first built for.
