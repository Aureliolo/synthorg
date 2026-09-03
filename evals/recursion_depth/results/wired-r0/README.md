# wired-r0: the loop measured through the product's own engine

Written 2026-09-02, closed 2026-09-03. The first recording in this directory
tree whose engine is the one the product ships: `build_agent_engine` over the
booted host, no collaborator missing, and a wiring report beside the journal
saying what was verified rather than a count of arguments. Every earlier
recording ran 8 of 51 collaborators (see `../root-causes/README.md`, which
withdrew its own verdict on that basis), so this is the first round that can
say which of its six root causes are the product's.

The verdict up front, in the same place the dossier put its own:

> **One cell, and the sweep stopped at it.** The product's loop reached 7 of
> 42 requirements with a live program, on 50.5M tokens over 16 sessions and
> 4 h 37 min. What decided that was not the loop's architecture: it was a
> 40-turn cap with no extension and a tool surface that refused, raced, or hid
> what the agents needed. All of that is fixed on this branch. Underneath it,
> the assembly did not act on findings it was handed by name (RC3 stands),
> and the model spent 95 to 100 percent of its output on hidden reasoning.
> The operator stopped the sweep before the repetitions on that evidence and
> the comparison in #2916, so no design change to the loop is made here, and
> the direction question is where the evidence went.

`smoke/` holds the cell: `cells.jsonl`, `progress.jsonl` (one row per
session, written as each returned), `wiring.json`, `depth_curve.{json,md}`
and `chart.svg`. It is re-scorable in place with `--rescore --out-dir
evals/recursion_depth/results/wired-r0/smoke`.

---

## What it took to run one cell

Five smoke attempts, each stopping on a gap between the harness and the
wired engine, each fixed at its stop point and committed before the next
(the round log carries one row per stop, `docs/reference/harness-round-log.md`):

1. A no-credential connection on litellm's OpenAI-SDK route sent no API key at
   all, so the local embedder could not be dispatched. Product fix: a placeholder
   key on that route, which also made every keyless local preset (LM Studio,
   vLLM) reachable for the first time.
2. The engine's entry hop refuses an `ASSIGNED` task not yet filed, so the contract
   session died before its first turn. The contract, merge and blind-pass
   sessions now run as `IN_PROGRESS` transients, the shape the product's own
   reviewer sessions take.
3. The harness shell tool carried no execution-record store, so the build/test
   oracle read every leaf as "no test run" and sent it back; and a run the host
   had already settled `FAILED` was resumed as if the infrastructure had died.
4. The host published no coordination pair, so the runtime was built without a
   coordinator and therefore without the peer-review gate. The pair is now
   published and the smoke reports the gate as a finding.
5. A delivered leaf under a contract carried the own-test gate's "decided
   nothing" note in the field the record refuses on a delivered unit, so all
   eight leaf rows of attempt 4 were discarded as they were paid for; and the
   contract stage took up a tree an earlier attempt's refused session had left,
   with no journal row behind it, so every leaf was seeded from an empty
   contract. The journal now decides what a resume takes up, never the disk.

Attempt 5 ran to its end and is the cell this file reads. It ran with the
harness still zeroing the product's turn extensions, which is why every leaf
ended at exactly 40 turns; that was found and fixed while it ran (the cap is
now 80 with the extensions live) and no later cell was recorded.

None of the five is a finding about the loop. All five are the cost of the
harness having measured a different engine for seventeen recordings.

## Treatment variables, as recorded

Every input is the committed manifest's own value; no per-run flag was passed,
so the recording is not a variant. Read back off the request bodies rather
than the configuration:

| variable | value | on the wire |
| --- | --- | --- |
| executor reasoning effort | `high` | sent on every executor request read (the smoke matched on the alias and read none; corrected to match the routed id) |
| executor sampling | temperature 0.7, `top_p` 1.0 | manifest, unchanged |
| reviewer reasoning effort | `high` | manifest, unchanged |
| compaction | fill 80%, text summariser | never fired: see the register |
| stagnation | `tool_repetition` | never fired: see the register |
| memory | `qwen3-embedding:0.6b` on a local connection, 1024 wide | probed at preflight, backend wired, retrieval ran per leaf: see the register |
| pair | two medium-band 1M cross-family connections | `cost_basis: unpriced`, so every cost figure is tokens |
| leaf concurrency | 4 | wall-clock only |
| unit turn cap | 40, with the product's extensions zeroed by the harness | the binding limit on all eight leaves, the contract (60) and both completed merge attempts (80) |
| unit token ceiling | 1.5M base + 250K per claimed requirement, capped at 4M; merge 5.5M | reached once: merge attempt 3 was parked on it |

## The verification register, answered on the wire

Evidence is the cell's `progress.jsonl`, the structured logs beside the run,
the recorded request bodies (for shape, never for absence: the tap drops a
few percent of lines under concurrency and counts them) and the kept trees.
"Attempt 4" is the discarded cell whose logs, transcripts and trees were kept
(`smoke-attempt4/` beside this recording's scratch, and the round log); it ran
every leaf to a product verdict before the harness lost its rows.

### Does a leaf get a review round?

**Yes when it finishes, and none finished here.** On attempt 4 the
post-execution path filed every finished leaf for review and the review
pipeline ran: one leaf was VERIFIED by the build/test oracle and then judged
by a roster Completion Reviewer at capability fit `match`, verdict
`approve_with_notes` with two findings, 249 s; one was sent back three times
by the oracle with no test run recorded and FAILED on rework exhaustion; five
hit the 40-turn cap and were FAILED with no review. On attempt 5 all eight
leaves hit the cap, so the pipeline was never asked (the wiring report says
exactly that). RC1's "one session, no review, no repair" was a property of
the seat, not the product. What the product does NOT do is review or repair a
leaf that ran out of turns, and with the extensions zeroed that was every
leaf.

### Why "no test run" when the agent ran its tests?

A run is recorded only when the line's exit status is the runner's own. The
harness shell sets `pipefail`, so `python -m pytest tests/ -q 2>&1 | tail -8`
IS recorded; `runner; echo "EXIT: $?"` is not, because the `;` hands the
status to `echo`. Attempt 5 recorded 40 test runs across the contract and
the leaves, pipelines included, so the habit the attempt-4 leaf had was not
general. The oracle's reason, the rework nudge and now the shell tool's own
description say what records a run.

### 108-turn merge at 225 messages with compaction on?

**The threshold is never reached at this pair.** No `compaction.*` event
fired in any of 16 sessions. The largest single request in the cell was 125K
tokens (a merge turn at 175 messages of context) against a threshold at 80%
of a 1M window, about 838K. The dossier's merge ran to 225 messages because
nothing bounded it; here the turn cap bounded it first.

### Abbreviate tool results or archive whole turns?

The per-result ceiling at entry (`engine.tool_output_max_chars`, 24,000)
fired ten times, eliding 700 to 6,500 characters each time. Whole-turn
archiving under compaction never engaged. Neither is "shorten OLDER results
as the context fills"; at these sizes nothing needed it.

### Does compaction protect the task block?

Not testable on a cell where compaction never fires, and not probed: the
sweep was stopped.

### Does the merge converge?

**No, and the mechanism is on the wire.** Three attempts: 80 turns to the
cap, 80 turns to the cap, then parked by the product on the 5.5M-token hard
ceiling (the operator-resumable path, which is the right outcome for a run
that has spent its budget). The roster reviewer rejected each in about a
minute with code-quoted findings. Rounds 1 and 2 named the same two critical
lines: `sqlcsv/join.py` dispatches to an `inner_join` it never defines, and
`exec.py` calls `join_rows` with two arguments where four are required.
Attempt 2 was briefed with both by name. It made 87 shell calls, read
`join.py` four times, edited through `sed` and here-documents, and wrote
nothing in 80 turns. Attempt 3 wrote `join.py` at turn 50 of 68, after over a
million characters of hidden reasoning across seven single-call turns, and
both lines are fixed in the kept tree; it then ran three ad hoc smoke scripts,
never the suite, and parked mid-call before writing its report. What one run
of the suite would have said survived it and all three read-only reviews:
`exec.py` names `Star` and `Aggregate` without importing them, so every query
raises `NameError` on the first line of `run_pipeline`, a defect the assembly
inherited from one leaf and made worse by dropping the one import that leaf
had. Round 3 rejected on the missing test run and a NULL-ordering
inconsistency between two modules. The final tree's own suite fails 50 of
255, and the oracle passes 7 of 42.

### Is anything left for incremental integration once the contract closed divergence?

The contract closed most of it: 7 of 27 shared modules diverged from the
agreed surface, against 11 of 14 in the corpus recorded without a contract.
What remained was `join.py` (one unit's signature and a missing function),
`errors.py` (one unit's added taxonomy), `exec.py` (a pipeline entry and a
`project` signature), and three units that added a name the others lack.
That is the work an assembly exists to do, and this one did not do it. The
merge spent 92% of its calls on shell (39% `cat`, 30% `diff` in attempt 1;
26% `python`, 20% `cat`, 16% `sed` in attempt 2), 6% on writes.

### Does stagnation catch a repeated probe?

No `stagnation.*` event fired in any session. The repeat column of the flow
report (identical call, arguments included) reads 0 to 10 per session, under
the detector's window and threshold. The 20x identical probe the dossier saw
did not recur.

### Memory across units

Descriptive at one cell. The backend wired on the declared embedder after the
first boot pass could not resolve it (`memory.embedder.unresolved` at boot,
`memory.backend.wired` once the setting was written); retrieval ran on eight
leaf starts, six skipped as below relevance, two injected. Whether it changes
anything is a controlled comparison this round did not run.

### Prompt caching

Skipped on every call (`model_lacks_caching_support`), 248 of 248 on
attempt 5. The dossier's "80.7M tokens never cached" is the whole input side on
this connection by construction, not a regression.

## Per-task sizing (the allocation mechanism)

Read off `budget.hard_ceiling.configured`. Each unit's token ceiling is sized
from what it claims, and what it spent is the journal's row:

| unit | claims | ceiling | spent | ended |
| --- | --- | --- | --- | --- |
| contract | n/a | 2.50M | 2.53M | 60-turn cap |
| architecture decision | 0 | 1.50M | 2.90M | 40-turn cap |
| joins | 2 | 2.00M | 2.34M | 40-turn cap |
| package skeleton and CLI | 3 | 2.25M | 3.18M | 40-turn cap |
| aggregation | 4 | 2.50M | 2.48M | 40-turn cap |
| ingest | 5 | 2.75M | 1.75M | 40-turn cap |
| rendering and end-to-end | 6 | 3.00M | 3.41M | 40-turn cap |
| SQL front end | 8 | 3.50M | 2.37M | 40-turn cap |
| binding and execution | 16 | 4.00M (cap) | 2.84M | 40-turn cap |
| merge (three attempts) | n/a | 5.50M | 26.2M | cap, cap, parked on the ceiling |
| roster reviewer (three sessions) | n/a | 5.50M | 2.84M | completed |

"Spent" is the journal's summed request tokens for the unit, which counts the
re-sent conversation on every turn and so exceeds a ceiling the enforcer
reads against a different figure. The turn cap ended every unit before its
token ceiling did, except the third merge attempt.

## Figures re-derived through the wired engine

From `scripts/report_merge_economics.py`, `report_session_flow.py --calls
--shell N` and `report_interface_divergence.py` over the kept run:

| figure | attempt 5 | the dossier's corpus |
| --- | --- | --- |
| tokens, cell | 50.5M over 16 sessions | |
| share of the cell in merges | 49% (26.2M, 228 requests) | roughly 70% |
| merge input to output | 27:1 | |
| merge tool calls | 92% shell, 6% write (222 shell, 9 edit, 6 write, 4 read) | 83% shell, 13% write |
| merge shell programs, attempt 1 | `cat` 39%, `diff` 30%, `for` 9%, `python3` 6% | 84% shell, half of it looking |
| leaf tokens | 10.6M over 320 requests, 4:1 input to output | |
| leaf tool calls | 64% shell, 32% write (222 shell, 61 edit, 51 write) | |
| review tokens | 2.84M over 88 requests, 55:1, 128 reads, and no shell | |
| thinking share of emitted text | 97 to 100% in every session kind | 95 to 100% |
| turns to the response cap | 4 of 718 calls, all on leaves | |
| shared modules diverged from the contract | 7 of 27 | 11 of 14, 11 of 12, 12 of 13 without one |
| dropped transcript frames | 230, counted | |

Coordination-derived routing is not measurable in this seat: the sweep
dispatches its own waves.

## The probes

- **Oracle gaming (score satisfied, artefact dead).** Not run: the
  sweep was stopped. The cell's own reading is the opposite disagreement,
  a LIVE program at 7 of 42, which the Deliverable column shows apart from
  the score as designed.
- **Memory across units.** Descriptive only; see the register.
- **Per-task sampling against the fixed pool.** The allocation mechanism
  sized every unit (table above); the comparison arm does not exist in this
  manifest and was not recorded.
- **Power at five repetitions.** Not run. One cell carries no interval, and
  the `detectable_factor` the report now prints beside the interval has no
  runs to compute over. #2844 stays as it is, since the floor it asks about
  was never measured.

## The decision this round records

None of the five outcomes the issue offered. The evidence that would have
chosen between them (five cap-1 repetitions on the fixed plumbing) was not
paid for, by operator decision on 2026-09-02, because the same evidence that
motivates fixing the plumbing argues that the inner loop is not where this
product's value lies: a mature harness with a frontier model would beat this
loop on this task by a wide margin, and a product that runs those harnesses
under an org chart, budgets and approvals now exists, which is #2916's
question. What this cell
does settle: RC1 and RC4 are not the product's; RC3 is, and it is the
assembly not acting on findings rather than nothing telling it; the INTEGRATE
stage keeps its design because nothing here separates its cost from the
turn cap's; leaf repair rounds and tool-result abbreviation are not
motivated by one cell that never reached either.

## Product findings this round surfaced

Read off attempts 4 and 5 by one analyst pass per session (a turn-by-turn
digest from `scripts/report_session_digest.py`, reviewed against a fixed
rubric), then confirmed against the structured logs and the kept trees. Each
is either fixed on this branch or named as what it is.

Fixed on this branch, each with its test:

- The credential scanner's "generic secret assignment" rule matched any
  `token = <eight characters>`, which is every tokeniser ever written. Four of
  eight attempt-4 leaves were refused on writing a parser; two spent their
  whole budget bisecting the refusal. The rule now reads the value's shape (a
  quoted literal, or a bare secret-shaped run), never the variable's name.
- A turn's tool calls all ran side by side, so two edits of one file in one
  turn raced and the second hunk was applied to text the first had replaced.
  Mutating calls now run in program order; only runs of read-only calls fan
  out (`READ_ONLY_ACTION_TYPES`).
- `write_file` refused a path whose parent did not exist unless told
  `create_directories`, which cost a `mkdir` turn in most sessions; missing
  parents are now created by default.
- An agent that called `write_file` by name before loading it guessed the
  parameter names three times; the always-in-context tool summary now names
  each tool's parameters.
- The shape of a test run the build/test oracle can see (the line's exit
  status must be the runner's own) was told to the agent only by the refusal,
  one rework round too late; the shell tool now states it up front, and the
  oracle's refusal and the tool's description share one sentence.
- The planning session's `subtasks` argument arrived as the TEXT of its JSON
  on five of its seven submissions and was refused with "is not of type
  'array'" each time (six of eleven turns); a value sent as JSON text is now
  decoded wherever the schema admits no text, and the reviewer's `"null"`
  test command (refused twice per round for naming a command called null)
  reads as no command.
- The harness zeroed the product's turn extensions, so a cap was a hard end
  rather than a park; removed, and a run that parks at its ceiling is
  recorded as parked rather than resumed.
- Every tool result carrying an angle-bracket tag was parsed as HTML and
  returned as its text, or as nothing when it opened with an XML declaration:
  two sessions read `.synthorg-grade.xml`, got an empty result, and spent 8
  and 12 turns on it. Outside this run the same door returned TypeScript
  without its generics, JSX without its markup, a here-document without its
  redirect and a diff without its lines, to an agent about to edit what it had
  read. A result is now rewritten only when it is an HTML document that hid
  something, and a clean document is returned byte for byte.
- The rework brief was built from the reviewer's summary alone, in all three
  review gates: the findings the verdict tool demands, and refuses a reject
  without, never reached the assembly, which was told "not mergeable" and
  had to find the two lines itself. Every gate now renders every finding into
  the hop, and the verdict schema says so; every round's first submission had
  carried its findings in the summary and been refused, one turn per round.
- The session-flow and digest reports read a non-streamed planning response
  as no calls at all; the wiring smoke compared two spellings of one
  embedder reference, demanded a policy engine where the product configures
  none, and matched reasoning effort on the alias the wire never carries.

Named, not fixed here:

- The assembly spends its budget orienting and does not verify what it
  writes (the register, above). Attempt 2 was briefed with two named lines
  and edited nothing in 80 turns; attempt 3 fixed both at turn 50 of 68 and
  never ran the suite, so the `NameError` in front of them reached the kept
  tree. Three read-only reviews read that `exec.py` in full and none traced
  `order_rows` back to the caller that raises.
- The sandbox's `diff` is BusyBox, which rejects GNU `-x`; the assembly lost
  six turns learning that before falling back to a hash comparison in
  Python. Adding `diffutils` to `docker/sandbox/apko.yaml` needs the lock the
  weekly workflow mints, which is why it is named here rather than changed.
- The destructive-command rule matches the text `rm -rf`, so a
  `find __pycache__ -exec rm -rf {} +` scoped away from `.children/` was
  refused twice. The rule reads the verb and not the target, which is what
  makes it a rule; two turns.
- The contract session (60 turns) wrote 15 modules and 46 pending tests
  covering all 42 requirements and never wrote `CONTRACT.md`, the one path
  the stage checks; the harness reads that as `contract_absent` and still
  seeds every leaf from the tree. It also pinned "tests via `unittest`" in one
  module while its own pending tests are pytest-style, and one leaf spent 18
  of 40 turns on that contradiction.
- One contract test file failed to collect because a fragment of the model's
  reasoning leaked into code (`fmt I think == "csv"`); caught and fixed by the
  same session three turns later.
- No leaf of attempts 4 or 5 wrote `.synthorg/unit/report.md`; every one
  ended at the 40-turn cap mid-tool-call with no closing reply.
- The architecture-decision unit was graded delivered on the tree-changed
  rule while its one declared artefact was absent and its four changed files
  were other units' modules: the rule is documented and deliberate, and
  vacuous for a unit that claims nothing.
- Reasoning to the response cap (131,072 output tokens, one turn, about
  twenty minutes of silence) happened four times in 718 calls, on three
  leaves (one of them twice in a row) and never on the merge, whose own
  fifteen-minute silences stayed under the cap; the stall watcher reports
  each correctly and the run continues. Merge attempt 3 emitted over a
  million characters of reasoning across seven single-call turns at effort
  `high` before its first write.
- The health prober refuses the cloud connection every minute ("cannot
  resolve a health-probe API key"): the harness registers that provider
  without a catalog connection, so there is no credential to resolve. A
  harness configuration artefact, not a prober defect.
- A provider request timeout exists per provider (`ProviderConfig.timeout`,
  unset by the harness pair, so litellm's 6000 s default applies) and nowhere
  as a live setting.
- Each work root derives its own deployment id, so a run killed mid-cell
  leaves containers a later run under another root never reclaims. Cleaned
  by hand between attempts.
- `BusyBox grep` in the sandbox rejects `--include`; `edit_file` refused
  hunks whose text had drifted; one `edit_file` was refused "Permission
  denied" on a file the same turn listed as world-writable, on a Windows
  host bind mount. Each cost a turn.

## How to re-read this cell

```
python scripts/report_session_flow.py --run run-7814bac6fa2e --calls
python scripts/report_session_flow.py --run run-7814bac6fa2e --kind merge --shell 12
python scripts/report_merge_economics.py .recursion-depth/work/run-7814bac6fa2e
python scripts/report_interface_divergence.py .recursion-depth/work/run-7814bac6fa2e
python scripts/report_session_digest.py --run run-7814bac6fa2e --out-dir <dir>
```

The work root is not committed (it is 110 MB of transcripts and nine trees);
the journal, the wiring report, and the curve are.
