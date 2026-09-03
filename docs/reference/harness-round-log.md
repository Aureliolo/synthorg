# The harness round log

Every recording under `evals/recursion_depth/results/`, what it measured, why
it stopped, and the engine wiring it ran under. This is the instrument's
output. The procedure that produces a row is the `harness-experiment` skill;
what a row has to prove is
[the wiring check](../design/recursive-decomposition.md#before-a-matrix-is-paid-for).

The log lives here rather than only in each recording's own README because a
recording's place in the sequence is the whole value of keeping it, and the
directory listing does not say which recording answered which question or
what it was measured under.

This is one of two round logs and they do not feed each other:
[the loop round log](loop-round-log.md) records the operator-seat runs of the
deployed product, and the site-wide count of how often that loop was driven
live and where it stopped is drawn from there alone. A harness cell here
reaching its merge changes nothing in that count, because the harness
dispatches its own waves and never runs the operator seat.

## The wiring column

Every recording below ran the harness's own hand-built engine, which passed
**8 of the 51** collaborators the product's boot path passes: no compaction,
no review pipeline, no approval gate, no policy engine, no budget enforcer, no
memory. Nothing could tell, because omitting a keyword argument looked exactly
like deciding against it. The corpus therefore measured an engine the product
does not ship, and the root-cause analysis built on it withdrew its own
verdict.

The harness now calls the product's own assembly, an engine is not
constructible with a collaborator missing, and a recording states its own
wiring in its report. A row written from here on names what the wiring report
verified rather than a count of arguments.

## The rounds

| recording | what it measured | why it stopped | wiring |
| --- | --- | --- | --- |
| `pilot` | seven cap-1 cells, the first measurement the harness produced; 254 turns and 6.68M tokens on a 1.5M ceiling | the pilot signal a later issue replicated did not survive re-verification: every zero in the kept record is a traced defect | 8 of 51 |
| `sweep-default-bare-r0`, `sweep-default-contract-r0`, `sweep-default-contract-r1` | the first default-manifest sweeps: one bare, two with the contract stage | each stopped after one or two cells on a harness defect recorded below | 8 of 51 |
| `sandbox-teardown-race` | two cap-1 cells at leaf concurrency 4 | the binder's sandbox list was shared across sessions, so the first leaf to finish tore down the sandboxes of the three still running; the journal measured the harness | 8 of 51 |
| `pre-transcript-fix` | the first cell where all eight leaves held a working shell: 6 of 8 delivered, 0 of 42 passed | the transcript recorder had the same shared-state race; the fix moved the commit and the journal identity pins it, so it cannot be resumed into | 8 of 51 |
| `leaf-delivery-false-negative` | one cap-1 cell scoring 41 of 42, abandoned on a sanity pass over its units | three leaves that wrote 4, 8 and 10 modules were recorded as undelivered: delivery asked whether a path the PLANNER guessed had changed | 8 of 51 |
| `merge-delivery-false-negative` | three cap-1 cells and one cap-2, 35 to 38 of 42 at cap 1 and 0 at cap 2 | not a depth effect: both root merges wrote a real package and skipped the markdown report, and delivery asked only whether a declared path had changed | 8 of 51 |
| `merge-test-hoisting` | three cap-1 cells and one cap-2, the cap-2 cell scoring zero | the sub-merges left their test files where the grader's suite run from the workspace root could not collect them | 8 of 51 |
| `contract-a`, `contract-b`, `control-a` | the contract stage against its control; shared modules diverged 11/14, 11/12, 12/13 without it and 0/21 with it | `contract-a` reads every leaf undelivered because the delivery gate ran the whole suite against tests the leaf was briefed to leave failing; the divergence numbers stand | 8 of 51 |
| `ceiling-3m` | one cap-1 cell at a 3M unit token ceiling against the same tree at 1.5M | 4.3x the cost from a 2x raise and fewer leaves delivered; abandoned after the first cell, kept as the only measurement of the ceiling | 8 of 51 |
| `reasoning-default` | no recording; the three depth-1 smoke cells re-read plus five probe completions | 95 to 100% of every session's emitted text was hidden reasoning; the executor's family defaults an absent effort to its most expensive tier | 8 of 51 |
| `loop-flow` | no recording; five earlier recordings read off the wire (the request bodies and the raw streams) | the contract changes the shape of a leaf (reads 3 to 36% of calls, edits 22 to 5%), and 84% of the calls a merge makes are shell, half of those looking one file at a time | 8 of 51 |
| `harness-audit` | no recording; the loop scored against four published harness results | three of eleven published techniques absent: context compaction with a pinned plan block, a pre-completion verification gate, and repetitions at the recommended floor | 8 of 51 |
| `root-causes` | no recording; the verdict over everything above | six root causes and one replacement, then the corrections below | 8 of 51 |
| `wired-r0`, smoke attempts 1 to 3 (no cell kept) | the first sessions through the product's own assembly: the plan, the contract and the leaves each refused or misjudged before a cell could finish | four gaps between the harness and the wired engine, each fixed at its stop point: a no-credential connection on litellm's OpenAI-SDK route sent no key at all, so the embedder preflight failed (product-wide: no keyless local preset could be dispatched); an ASSIGNED task not yet filed is refused at the engine's entry hop, so the contract, merge and blind-pass sessions now run as IN_PROGRESS transients; the harness shell tool carried no execution-record store, so the build/test oracle read every leaf as "no test run" and sent it back; a run the host had already settled FAILED was resumed as if the infrastructure had died | no report written, no cell finished |
| `wired-r0`, smoke attempt 4 (journal, logs and trees kept beside the session) | the first cell in which every leaf ran to a product verdict: 8 leaves, 5 stopped at the 40-turn cap and FAILED with no review, 1 sent back three times by the build/test oracle and FAILED on rework exhaustion, 1 verified by the oracle and approved with notes by a roster Completion Reviewer at capability fit `match`, which answers the register's first question on the wire; no stagnation, compaction or tool-output abbreviation event fired in any session; prompt caching was skipped on every one of 298 calls | two harness defects, both fixed: a delivered leaf under a contract carried the own-test gate's "decided nothing" note in the field the record refuses on a delivered unit, so all eight leaf rows were discarded as they were paid for; and the contract stage took up a tree an earlier attempt's refused session had left, with no journal row behind it, so every leaf was seeded from an empty contract and graded on tests never written. One product finding behind the oracle's three rejections: the agent ran its suite as `runner 2>&1; echo "EXIT: $?"`, and the `;` hands the line's status to `echo`, so no run was recorded (a pipeline under `pipefail` is, which is how the verified leaf earned its evidence) and the reason it was sent back with named neither the mechanism nor the fix (both now do) | coordinator and peer-review gate built, read off the host; no report written, the cell was discarded before the probe ran |
| `wired-r0`, smoke attempt 5 (the cell kept: `smoke/cells.jsonl`, `progress.jsonl`, `wiring.json`, `depth_curve.md`, the logs and trees beside the session) | the first cell through the product's engine that ran to its merge: 4 h 37 min, 50.5M tokens, 16 sessions; a plan of 8 units in 11 turns (seven submissions, five refused because the array arrived as JSON text); a contract of 15 modules and 46 pending tests that hit its 60-turn cap without writing `CONTRACT.md`; 8 leaves, every one at the 40-turn cap with extensions zeroed by the harness, 2 graded delivered (the SQL front end on its own tests, the architecture decision on the tree-changed rule with its one declared artefact absent); a merge of three attempts (80 turns, 80 turns, then parked by the product on its 5.5M-token hard ceiling), rejected three times by a roster Completion Reviewer at capability fit `match`, rounds 1 and 2 on the same critical lines (an undefined `inner_join` and a wrong-arity `join_rows` call), which attempt 2 was briefed with by name and did not write in 80 turns of `sed` and here-documents, and attempt 3 fixed at turn 50 of 68 without ever running the suite, leaving a `NameError` on every query's first line that three read-only reviews also missed; oracle 7 of 42, program LIVE, leaf survival 1 of 8 claimed, 7 of 27 shared modules diverged from the contract; the merge took 49% of the tokens at 27:1 input to output, 92% shell calls, 100% hidden reasoning | the register, answered: no leaf was offered for review because none finished; compaction never fired (the largest request was 125K tokens against a threshold at 838K); tool-output abbreviation fired ten times; stagnation never; the empty-turn correction fired twice; memory was wired and retrieved (8 starts, 2 injections); caching skipped on every call; 40 test runs recorded, pipelines included. Three wiring findings read wrong and were corrected: the memory check compared two spellings of one reference, the policy engine was demanded where the product configures none, and reasoning effort was matched on the alias rather than the routed id. The operator stopped the sweep at this point (2026-09-02) after the Paperclip comparison (#2916): attempt 6 and the cap-1 repetitions were not run, so no power result exists and #2844 stays as it is | every treatment the manifest declares, read off the host and the wire: memory, compaction, stagnation, budget, review pipeline, approval gate, peer review passed; policy engine absent as configured; leaf review, reasoning effort, and caching unverified on this cell |

## What the corpus could and could not say

Every number above was measured honestly and none of them measures the
product. Three of the six root causes name a mechanism the harness had not
wired rather than one the product lacks: nothing managed context because the
harness passed no compaction callback, no review pipeline ran per unit because
the harness passed none, and the budget signal that was measured firing was
the task's own token ceiling standing in for an enforcer that was not there.
Which of the six survive is what the next recording exists to answer, and it
is the first that can.

Two corrections to the dossier were established while closing the gap, and
are recorded in its own README: the stagnation detector was never a parity
gap, because the product's default was also off, which is why the default has
changed rather than the harness; and eleven of the fields the dossier counted
as missing were not passed by the boot path either, so the real gap was
forty-three collaborators, and after the assembly change it is zero.

## How to add a row

A round's row is written when the round stops, and it is written whether or
not the sweep reached its deep end. Name the recording's directory, state the
treatment as the flag that set it, quote what the wiring report verified
(`<out-dir>/smoke/wiring.json`: a finding is passed, failed or unverified, and
an unverified one is named as such), say how far the curve got and open the
stop reason with the mechanism rather than the symptom.

The round's own success metric is **whether the stop point moved**, not how
many findings it produced. A recording that stops on the harness has measured
the harness; the row says so, and the next round's question is whether the
same stop recurs.
