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

## How to add a row

A round's row is written when the round stops, and it is written whether or not
the round reached the tail. Record the stage it stopped at against the edges in
[what each edge owes you](../guides/end-to-end-run.md#what-each-edge-owes-you),
and state the reason in one sentence that names the mechanism rather than the
symptom.

The round's own success metric is **whether the stop point moved downstream**
of the previous round's, not how many findings it produced. Rounds have been
highly productive and still not converged on the question they exist to answer.
