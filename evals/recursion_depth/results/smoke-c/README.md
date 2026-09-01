# Smoke cell C: executor 0.6 / 0.95

One depth-1 gated cell, recorded 2026-09-01 as the third of three concurrent
probes sweeping executor sampling before any matrix spend. 0.6 is the floor
DeepSeek and Qwen publish for a reasoning model and the hedge against 1.0 proving
too hot for a hundreds-of-turn agentic loop, which is the corner where the
published evidence is thinnest.

## What it measured

| | value |
| --- | ---: |
| achieved depth | 1 |
| leaves | 8 |
| **requirements satisfied** | **19 of 42** |
| sessions | 15 (10 journalled) |
| tokens | 30,936,924 |
| spend | unpriced (flat-rate connection) |

Satisfied: R01, R02, R04, R10, R11, R13, R14, R17, R19, R20, R21, R26, R27, R28,
R29, R30, R37, R40, R41.

**Read this beside its siblings**, which ran the same specification, depth, arm,
reviewer configuration and 42 requirements:

| Cell | executor | satisfied | tokens | merge attempts |
| --- | --- | ---: | ---: | ---: |
| A | 0.7 / 1.0 | 39 | 19,604,624 | 1 |
| B | 1.0 / 0.95 | **40** | 30,166,347 | 3 |
| **C** | **0.6 / 0.95** | **19** | **30,936,924** | **3** |

**C is the outlier, not A.** A and B land one point apart; C collapses 21 points
below both while spending the most. So the smoke's result is bimodal ("usually
about 40, occasionally catastrophic") rather than a smooth 20-point spread, which
matters for the matrix: adding repetitions dilutes a collapse but does not detect
one, and this collapse has a traceable mechanism.

## The treatment

| | executor | reviewer |
| --- | --- | --- |
| temperature | **0.6** | 0.6 |
| `top_p` | **0.95** | 0.95 |
| `reasoning_effort` | unset | `high` |
| `max_tokens` | 131,072 | 65,536 |

Verified on the recorded request bodies and the journal header, not assumed. The
reviewer's configuration is byte-identical to cells A and B, down to the
connection sha256, so nothing about the reviewer varies along the axis. Sandbox
`ghcr.io/aureliolo/synthorg-sandbox@sha256:af899636...`, pinned by digest.

## Per leaf

| Leaf | delivered | terminated |
| --- | --- | --- |
| Decide engine architecture and shared contracts | no | completed |
| CSV ingest and column typing | **yes** | completed |
| SQL front end: lexer and parser | **yes** | budget_exhausted |
| Semantic checks and the row pipeline | no | budget_exhausted |
| Joins: INNER and LEFT with qualified references | no | budget_exhausted |
| Aggregation: COUNT, SUM, AVG, MIN, MAX, GROUP BY | no | budget_exhausted |
| Output rendering: table, csv and json | **yes** | completed |
| CLI surface, exit codes and end-to-end acceptance | no | budget_exhausted |

**Five of eight leaves ran out of budget** at `unit_token_ceiling: 1500000`, and
only three delivered. The merge therefore inherited five partial implementations.

Note the first leaf: a whole unit was spent deciding "shared contracts", produced
one file, and delivered nothing. The plan asked for a contract and got a document
rather than code, which is the difference between this harness and the product's
`SKELETON` stage.

## What the merge did

```
produced=True  workspace_files_changed=73  delivered=False
attempts=3 (the cap)  terminations=['budget_exhausted', 'budget_exhausted', 'budget_exhausted']
```

**All three merge attempts exhausted their budget.** Each re-authorises a full
5,500,000 tokens, so the merge phase alone had 16.5M authorised and used it
without ever finishing.

**It carried every one of its leaves' tests up**, unlike cell A:

carried up 10 of 10 (`test_aggregation.py`, `test_cli.py`, `test_e2e.py`,
`test_executor.py`, `test_ingest.py`, `test_join.py`, `test_lexer.py`,
`test_parser.py`, `test_render.py`, `test_semantics.py`), invented none, dropped
none.

So C did the job A declined, and the metric does not reward it: the oracle grades
behaviour with its own held-out tests and never reads the unit's.

## Why it scored 19: the same two modules, across all three attempts

Every merge failure in this cell was one class, a name the caller expected and
the writer spelled differently. Binned by wall-clock minute from the run's
`logs/synthorg.log`, which `.gitignore` excludes (40 MB across the three cells),
so the counts below ARE the record rather than a pointer to one:

| window | failures | names |
| --- | ---: | --- |
| 01:51 to 01:54 | 14 | `ColumnNotFoundError`, `Executor`, `sqlcsv.aggregation`, `sqlcsv.csvio` |
| 03:19 | 2 | `sqlcsv.aggregation`, `sqlcsv.csvio` |
| 03:29 | 2 | `sqlcsv.aggregation`, `sqlcsv.csvio` |
| 04:24 to 04:26 | 3 | `sqlcsv.aggregation`, `sqlcsv.csvio` |

**`sqlcsv.csvio` and `sqlcsv.aggregation` were unimportable in every window,
across 2.5 hours and all three attempts.** They are not missing files: they are
modules the children wrote under other names, and resolving one means choosing a
vocabulary and rewriting every caller of the other seven children's.

11 of the 12 modules more than one child defined disagree on their exported
names, which is the same figure as cells A (11 of 11) and B (10 of 11). It is
structural, not a sampling effect.

**The import failures are not themselves why the score is 19.** The oracle runs
its own held-out tests against the CLI and never imports the unit's modules, so
`sqlcsv.csvio` being unreachable breaks C's own suite rather than the grading.
What the failures indicate is that the merge never finished reconciling the
package, and the score reflects the unfinished package.

**C's passing set is a strict subset of BOTH other cells'**: it satisfied nothing
that A missed and nothing that B missed. Meanwhile A and B are not nested (A
uniquely passes R18 and R38; B uniquely passes R06, R22 and R35), and every one of
the 42 requirements is passed by at least one cell.

That rules out a noisy grader. Ambiguous requirements or order-dependent grading
would give ragged overlap and leave some requirement unreachable; instead one tree
is cleanly dominated, two comparable trees differ by a handful each way, and
nothing is unpassable. The oracle is precise. What collapsed is the software this
cell's loop produced.

## Defects this cell surfaced

- **The harness has no contract stage while the product mandates `SKELETON`.**
  This cell is the clearest instance: it spent a whole leaf on "shared contracts"
  and still could not import two of its own modules three attempts later.
- **A merge retry fixes structural omissions and never semantic divergence.**
  Three attempts at 5.5M each, all `budget_exhausted`, with the same two module
  names blocking the first and the last.
- **The metric cannot see whether tests were carried up.** A carried 0 of 9 and
  scored 39; B carried 12 of 12 plus one invented and scored 40. Two trees one
  point apart, one with thirteen test files and one with none. (This cell carried
  10 of 10 and scored 19, but for an unrelated reason: its package was
  incomplete.)
- **Repair rounds are not held equal across cells.** C got 3 merge attempts and A
  got 1, decided by how thoroughly each reviewer happened to check (C's reviewer
  ran the suite 19 times, A's once) on a byte-identical reviewer configuration.

## Cost

30,936,924 tokens against the manifest's declared ~14M for a cap-1 cell:
**121% over, more than double the cheapest cell class in the matrix.** The three
smoke cells together have consumed 180% of the ~42M the plan projected for them.

The bill is input, not generation: 28,386,239 input against 2,550,685 output over
734 calls, a ratio of 11 to 1. Every file the merge read is re-sent on every later
turn, so cost is quadratic in the length of the read phase. Raising `max_tokens`
cannot reduce it and neither can more budget; shortening what the merge has to
read before it can start is the only lever with leverage, which is the argument
for a contract stage stated as a cost figure.
