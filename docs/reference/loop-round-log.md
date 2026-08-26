# The loop round log

Every live end-to-end run of the general loop, how far it got, and why it
stopped. This is the instrument's output. The procedure that produces a row is
[the end-to-end run](../guides/end-to-end-run.md); what a row has to prove is
[the evidence contract](../design/initiative-tail.md#what-proves-the-tail-ran).

The log lives here rather than only in a status document because a round's
result is the whole value of running it, and rounds have been lost before by
having nowhere to write them down.

## The brief

Unchanged across every round, verbatim:

> I want a tetris game I can play in the browser, with a shared leaderboard.

That is the whole thing. Single or multiplayer, how many modes, the timeline,
where it runs, how the leaderboard persists, what "done" means: all of it is
left for the org to ask. A brief that arrives pre-specified proves nothing
about an interview, and a silence where a question should be is itself a
finding.

**Do not change the brief.** The log is one table across rounds, so a changed
brief silently changes what every number in it means.

The arms differ by exactly one answer, given when the interview asks how we
will know it is done:

| arm | the answer | why |
| --- | --- | --- |
| control | the finished game must pass an automated end-to-end test that drives it in a real browser | the sandbox ships no browser and no display, so each part can pass its own tests while the assembled whole cannot be evidenced |
| honest | the game logic has an automated test suite that passes, with no network install | the sandbox can evidence this, so INTEGRATE can assemble, run the suite, and clear the oracle |

## The rounds

| round | how far it got | why it stopped |
| --- | --- | --- |
| 1-2 | intake to approved plan in 90.8s | all five tasks dead 1.85s later; the native backend could not authenticate |
| 3 | planning and review only | Windows event-loop split (Selector for psycopg, Proactor for subprocesses): zero agent tools, so the tail was structurally unreachable |
| 4 | 7 subtasks, a 4-reviewer panel, 6 items dispatched, 2 review gates opened | 18 collapses. The killer: a reasoning model answers on two channels and the loop read one, so 47 productive turns were discarded because the last one said nothing out loud |
| 5 | n/a | provider incident: the top-tier pair returned 503 for an hour and took 311s to refuse a five-token reply while the prober called it healthy |
| 6 | plan, dispatch, execution, review gates | 36 collapses, recorded before anything was fixed |
| 7 | 3 waves dispatched, 4 items ran real work (243 tool invocations on one), the all-dead plan correctly derived `mixed_dead` and scheduled a replan | every item failed completion review for having delivered nothing: the review reads the deliverable from a frame store written after the review has already ruled, so a first run can never have one. The replan then exhausted its decomposition retries |
| 8 | charter intake to approved plan, waves dispatched, real work executed, and a parked question answered from the plan page landed on the plan verbatim | replan hit the generation cap: 3 items completed, 1 failed, 3 parked `dependency_failed`, the plan left `executing` while the rollup re-scheduled a replan the trigger refuses, on every cadence, indefinitely |
| 9 | the whole product driven through the browser as an operator; 59 findings | plan repair could not converge. The parser broke on the first graph violation, the planner regenerates the whole plan with fresh ids on a targeted correction, and "Request changes" burned all twelve turns, was rejected seven consecutive times by one rule on seven different pairs, then returned 500 after 5m17s |
| 10 | charter intake, a five-question interview that asked for a timeline unprompted, and the first recursive decomposition the product has ever run: 39 planning sessions, 76 plan corrections that converged, depth 4 against a cap of 5, `unsplit_count=0` on every finished node, and one workstream alone returning 40 leaves | one planning session outran its own ceiling and took the whole tree with it. Session 39 ran 599.7s against `coordination.decomposition_timeout_seconds` at 600, which raises the non-retryable `DecompositionTimeoutError`, and nothing between that raise and the plan absorbs it: plan `planning -> failed`, objective task `created -> failed`, and all 39 levels discarded after 1h 48m and 2.3M tokens. The graceful bound that returns a partial tree was two sessions from firing (`sessions_remaining=2` of 40) |
| 10b | the same brief re-run against a fix for the ceiling, on a three-question interview; 3 planning sessions, 13 subtasks, 3 levels | the same defect through a **third** bound the fix did not cover. A depth-2 session spent all 12 turns on `search_memory`, got `ranked_count=0` on all 17 calls, never once called `submit_decomposition_plan`, and no stagnation detector fired (`extensions_granted=0`). Turn exhaustion raises the **base** `DecompositionError`, which the parent does not absorb because that type is also every genuine fault, so one stuck node discarded the tree again after 3m 56s |
| 10c | re-run against fixes for all three bounds. 21 minutes, ~21 planning sessions, a tree to **depth 4** with subtrees of 20, 15, 12 and 11 leaves, and **both previously-fatal bounds absorbed live**: a wall-clock ceiling at depth 3 and turn exhaustion at depth 2, each costing one unit's split rather than the tree | the two caps that size a level contradict each other, and the planner cannot obey both. At the last permitted depth the atomicity gate cannot ask for depth, so it orders "split them into more units AT THIS LEVEL" while naming only per-unit limits; it has no access to `max_subtasks` and never mentions it. The planner complied, produced 11 units against a cap of 10, and `DecompositionSubtaskLimitError` failed the whole tree. Two owners for "how many units may this level have", and the quieter one wins fatally |

## Where the stop has moved

Nothing has entered `INTEGRATING`, so neither half of
[the evidence contract](../design/initiative-tail.md#what-proves-the-tail-ran)
has been produced. What has changed is where the runs die.

Rounds 1 to 5 died on the deployment: authentication, an event-loop split, a
reasoning-model channel, a provider incident. Rounds 6 to 9 died in planning and
review. Round 10 is the first to die on the far side of planning, with a tree
that had converged, and the first whose stop is a bound rather than a broken
mechanism.

Round 10 is also the first round where the thing being tested was new: recursive
decomposition shipped on by default days earlier, and had never run in the
product. Its own machinery worked. What did not was the interaction between a
per-node bound and a tree of 39 nodes, which no round before it could have
reached.

Its second attempt is the more useful half, because it turned one instance into
a rule. A child planning session can end without a plan three ways: the planner
declines to split, the session outruns its wall-clock ceiling, or the session
runs out of turns. The first was already absorbed by the level that asked for
it, the second was absorbed by the fix attempt 1 produced, and the third was
not, because turn exhaustion raises the base `DecompositionError` that every
genuine fault also raises. **A per-node bound has to declare what it does to the
tree above it, and a fix written from one observed instance covers one bound.**

Attempt 2 also caught the layer beneath: the session was not merely slow, it was
stuck, repeating one tool call that returned nothing every single time, and no
stagnation detection fired before the turn budget ran out. Absorbing the failure
keeps the tree; it does not make the node's plan appear.

Attempt 3 proved both fixes in one live run, absorbing a wall-clock ceiling at
depth 3 and turn exhaustion at depth 2 while the tree kept every other level,
and then died on something different in kind. The first two stops were a node
failing and the tree not surviving it. The third is **the system issuing an
instruction it will then refuse**: at the last permitted depth the atomicity
gate orders the planner to widen, the width cap kills the level for widening,
and neither knows the other exists. Compliance was fatal and non-compliance was
rejected, so no amount of retrying could resolve it.

That is worth stating plainly because it also corrects this log. Finding F4,
"the breadth cap and the depth ban fight each other", was recorded as closed on
the evidence that a tree had replaced ten coarse items. That is true of the
original symptom and false of the finding's own words: the two caps still
fight, and they fight hardest at the last level, where the depth ban is
absolute.

## How to add a row

A round's row is written when the round stops, and it is written whether or not
the round reached the tail. Record the stage it stopped at against the edges in
[what each edge owes you](../guides/end-to-end-run.md#what-each-edge-owes-you),
and state the reason in one sentence that names the mechanism rather than the
symptom.

The round's own success metric is **whether the stop point moved downstream**
of the previous round's, not how many findings it produced. Rounds have been
highly productive and still not converged on the question they exist to answer.
