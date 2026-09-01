# Smoke cell A: executor 0.7 / 1.0

One depth-1 gated cell, recorded 2026-09-01 as the first of three concurrent
probes sweeping executor sampling before any matrix spend. This cell is the
status-quo baseline: 0.7 temperature and 1.0 `top_p` are what every session ran
at before the sampling work in this branch, so it is the point the other two are
measured against rather than a candidate in its own right.

## What it measured

| | value |
| --- | ---: |
| achieved depth | 1 |
| leaves | 8 |
| **requirements satisfied** | **39 of 42** |
| leaf-work survival | 20 of 22 claimed (0.909) |
| sessions | 11 |
| tokens | 19,556,864 |
| spend | unpriced (flat-rate connection) |

**39 of 42 beat every prior recording** (previous best 38 across every kept run,
band 23 to 38). It was the highest in this smoke until cell B finished at **40**.

The three misses are unrelated to each other: R06 (what the tokeniser refuses),
R22 (WHERE equality and inequality), R35 (table format prints a padded header).
None is intrinsically hard: **cell B passed all three.** Conversely A uniquely
passed R18 and R38, which B missed. Two comparable trees differing by a handful in
each direction is what draw noise at the margin looks like.

The recursion-depth plan expected 0 of 42 here, citing the pilot. That figure is
an artefact of the pilot's own broken depth-1 cells and should not be used; see
`../merge-delivery-false-negative/` and `../pre-transcript-fix/`.

## The treatment, which is what this probe exists to pin

| | executor | reviewer |
| --- | --- | --- |
| temperature | **0.7** | 0.6 |
| `top_p` | **1.0** | 0.95 |
| `reasoning_effort` | unset | `high` |
| `max_tokens` | 131,072 | 65,536 |

Every value above was verified on the recorded request bodies rather than
assumed. The reviewer's `reasoning_effort: high` reaches the wire AND takes
effect: its responses carry `reasoning_content`, which is the first time graded
reasoning has worked on this deployment. Sandbox image
`ghcr.io/aureliolo/synthorg-sandbox@sha256:af899636...`, pinned by digest.

## Per leaf

| Leaf | delivered | claims | lost |
| --- | --- | ---: | --- |
| CSV ingest and column typing | yes | 5 | - |
| SQL lexer | yes | 4 | R06 |
| Parser: grammar to AST | no | 6 | - |
| Architecture and module contracts | no | 0 | - |
| Joins: INNER and LEFT | yes | 2 | - |
| Binder and execution engine | no | 18 | R22 |
| Output rendering: table, csv, json | yes | 5 | R35 |
| CLI surface, exit codes | yes | 6 | - |

**The tree is severely unbalanced.** One leaf carries 18 of the 42 requirements
and another carries none. That is the report's "planner-declared sizing" caveat
made concrete, and it is also why five of the eight leaves ran out of budget: an
18-claim unit cannot finish inside `unit_token_ceiling: 1500000`.

## What the merge did

```
produced=True  workspace_files_changed=16  attempts=2  turns=77
tokens=9,014,167  input=8,725,834  output=288,333
terminations=['budget_exhausted']
verdict=approve_with_notes  delivered=False
detail="the merged tree's own tests did not pass: the suite collected no tests"
missing_declared_paths=['.synthorg/merge/report.md', '.synthorg/merge/end-to-end.txt']
```

**One merge attempt, approved on the first pass.** `attempts=2` is sessions
(merge plus review). It exhausted its 5,500,000 budget in that single attempt,
which is why the report was never written and the test wiring was left broken.

**This merge brought up NO tests, and that is why it scored well.** The assembled
`sqlcsv/` package has 10 modules and 2,080 lines, and `tests/` contains **zero
files**.

The tests existed and were discarded by name. This cell's leaves wrote nine test
files, all of which stayed in `.children/`: `test_cli.py`, `test_cli_errors.py`,
`test_engine_smoke.py`, `test_errors.py`, `test_ingest.py`, `test_joins.py`,
`test_lexer.py`, `test_render.py`, `test_unit.py`.

Matched by filename against the other cells, cell C carried up all ten of its
leaves' tests and cell B carried up all twelve of its own AND wrote an additional
one. The merge brief instructs the agent to bring the pieces' tests up with their
code (`merge.py:403-407`); two of three merges did, and this one did not.

That single fact explains the whole record above: the suite collected no tests
because there were none, `delivered=False` follows from that, and the report went
unwritten because the merge stopped after assembling the package.

**And the oracle still scored it 39 of 42**, because the oracle grades behaviour
with its own held-out tests and never reads the unit's.

**But skipping the tests did NOT buy a higher score.** Cell B carried up all
twelve of its leaves' test files, wrote a thirteenth, spent the full three-attempt
repair budget, and scored **40**, one point better. So the honest reading is not
that the metric rewards omitting verification; it is that the metric **cannot see
the difference**: 39 and 40, one tree with thirteen test files and one with none.

What skipping bought was cost. A reached 97.5% of B's score for 65% of B's tokens
(19.6M against 30.2M), because reconciling ten to thirteen test files written
against eight mutually incompatible interfaces is the expensive part, and it cost
B and C three merge attempts each.

**Read this cell's 39 of 42 with that attached.** The score is real and the
software does work, but it was achieved by assembling a working package and
declining to verify it, so the number is not comparable to cell B's 40, which was
achieved with all thirteen test files in the tree.

**The harness anticipated this and says so**, which narrows the complaint.
`_delivery`'s docstring describes the case exactly ("a merge that assembled the
code and left the pieces' tests where it found them reports a failing check while
holding a complete package. That is why the two travel separately"), and
`depth_curve.md`'s per-merge table publishes the consequence as `Delivered | no`
beside this cell's 39. So the full report does not mislead. What is missing is
narrower: the CURVE carries no trace of it, and `delivered` is one bit, so it
cannot separate "carried 12 of 12 and two fail" from "carried 0 of 9", which is
precisely the distinction between this cell and the other two.

The merge did do real repair rather than stitching: three of eight leaves never
delivered and five ran out of budget, and the undelivered 18-claim leaf lost
exactly one of its eighteen requirements.

## Defects and findings this cell surfaced

Figures below are quoted from the run's `logs/` and workspace trees, neither of
which is committed (`.gitignore` excludes `logs/`, 40 MB across the three cells;
`--keep-workspaces` trees live under `.recursion-depth/`). The numbers here are
therefore the record, not a pointer to one.

- **The harness has no contract stage while the product mandates one.**
  `PlanStatus.SKELETON` sits between APPROVED and EXECUTING with no
  `APPROVED -> EXECUTING` edge; `evals/recursion_depth/` goes plan to leaves to
  merge. Measured consequence: 11 of 11 modules defined by more than one child
  disagree on their exported names, `errors.py` is written by all eight children
  with eight vocabularies, and `lexer.py` exports `lex`, `tokenize` and
  `tokenise` in three of them.
- **Lazy tool loading is advisory, not enforcing.** A tool executes when called
  by name whether or not it was advertised and without `load_tool`. Offered-tool
  counts measure nothing; read the calls.
- **The transcript tap corrupts under concurrency** (~8% of lines) and records a
  streamed response as the raw SSE frame stream, so any analysis treating it as
  text measures the wire format. Per-response usage comes from
  `logs/cost_usage.log`.
- **The 131,072 executor ceiling is correct and must not be raised.** p50 236
  tokens, p95 27,972, p99 87,140; the only responses reaching the cap are
  degenerate loops tailing into `5 5 w 5 5 w`.
- **The reviewer does not starve.** Reasoning runs 39 to 4,309 characters per
  turn against a 65,536-token budget and the verdict is emitted normally, so the
  reviewer's `max_tokens` needs no change either.

## Cost, for the matrix

19,604,624 tokens (cost log; the journal's 19,556,864 omits calls booked outside
a unit) against the manifest's declared ~14M for a cap-1 cell and a ~15.5M median
across every prior recording. **40% over, on a clean run with no merge retry.**

The overrun is the merge: 9,014,167 of it, 46% of the cell, for ONE attempt plus
its review. `attempts=2` on the unit record counts sessions, not merge attempts,
which the report's own "Merges 1 | Sessions 2" confirms.

Leaves are near-fixed at roughly 1.52M each because they run into
`unit_token_ceiling` rather than finishing.

**The bill is input, not generation.** The merge spent 8,725,834 input tokens
against 288,333 output, a ratio of 30 to 1 and 96.8% of its spend, because every
file it read is re-sent on every later turn. Cell-wide the ratio is 9 to 1. So
raising `max_tokens` cannot reduce cost here, and neither can more budget: the
levers are shortening the read phase (a contract stage) and not re-reading on a
retry.
