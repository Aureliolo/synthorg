# Smoke cell B: executor 1.0 / 0.95

One depth-1 gated cell, recorded 2026-09-01 as the second of three concurrent
probes sweeping executor sampling before any matrix spend. 1.0 with `top_p` 0.95
is the figure this executor's vendor publishes for evaluation, and the top of the
swept range.

## What it measured

| | value |
| --- | ---: |
| achieved depth | 1 |
| leaves | 8 |
| **requirements satisfied** | **40 of 42** |
| sessions | 15 (10 journalled) |
| tokens | 30,166,347 |
| spend | unpriced (flat-rate connection) |

Missing only **R18** and **R38**.

**40 of 42 is the best score this harness has recorded at any depth.** The
previous best was cell A's 39 in this same smoke, and before that 38 across every
kept recording.

**And it earned it doing the full job.** B carried up all twelve of its leaves'
test files, wrote a thirteenth, and spent the full three-attempt repair budget.
Cell A scored 39 having discarded all nine of its leaves' tests in a single merge
attempt. So on this evidence skipping verification bought nothing in score.

## The treatment

| | executor | reviewer |
| --- | --- | --- |
| temperature | **1.0** | 0.6 |
| `top_p` | **0.95** | 0.95 |
| `reasoning_effort` | unset | `high` |
| `max_tokens` | 131,072 | 65,536 |

Verified on the recorded request bodies and the journal header. The reviewer's
configuration is byte-identical across all three cells, down to the connection
sha256. Sandbox `ghcr.io/aureliolo/synthorg-sandbox@sha256:af899636...`, pinned by
digest.

## Per leaf

| Leaf | delivered | terminated |
| --- | --- | --- |
| Choose the SQL engine architecture | **yes** | completed |
| Package shell, shared contracts and CLI skeleton | **yes** | completed |
| CSV ingest and column typing | no | budget_exhausted |
| SQL front end: lexer, AST and parser | **yes** | completed |
| Output renderers: table, csv and json | **yes** | completed |
| Joins: inner and left | **yes** | budget_exhausted |
| Query engine: planning and execution | no | budget_exhausted |
| CLI wiring, exit-code contract and end-to-end | no | budget_exhausted |

Five of eight delivered, four exhausted their budget at
`unit_token_ceiling: 1500000`. This is the healthiest leaf phase of the three
(A delivered 5, C delivered 3).

## What the merge did

```
produced=True  workspace_files_changed=37  delivered=False
attempts=3 (the cap)  terminations=['budget_exhausted', 'budget_exhausted', 'budget_exhausted']
```

Assembled tree: **16 modules, 2,313 lines, 13 test files.**

Tests: carried up 12 of 12 (`test_cli.py`, `test_contracts.py`, `test_decision.py`,
`test_endtoend.py`, `test_ingest.py`, `test_join.py`, `test_lexer.py`,
`test_nosqlite.py`, `test_parser.py`, `test_render.py`, `test_sql_unit.py`,
`test_tree.py`), invented one (`test_cli_queries.py`), dropped none.

`delivered=False` because the merged tree's own suite does not pass, not because
nothing was assembled. That is the flag working as designed: `produced` and
`delivered` travel separately precisely so an assembled-but-failing package is
distinguishable from an empty one.

## The one thing a retry demonstrably fixed, and the one it did not

Binned from the run's `logs/synthorg.log`, which `.gitignore` excludes, so the
counts below are the record rather than a pointer to one:

| window | names |
| --- | --- |
| 03:07, 03:11, 03:12 | `SemanticError`, `EXIT_NOT_WIRED`, `render`, `tests.conftest` |
| 04:05 | `SemanticError`, `EXIT_NOT_WIRED`, `tokenise` |

**Fixed by the retry**: `tests.conftest`, absent from the 04:05 window, and the
merged tree does now hold `tests/conftest.py`, carried up from the children. A
forgotten file is one visible gap and the next attempt copies it.

**Not fixed**: `SemanticError` and `EXIT_NOT_WIRED` span 03:07 to 04:05 unchanged.
These are not missing files but one concept the children named several ways
(`sqlcsv.lexer.tokenise` is the same three-way `lex`/`tokenize`/`tokenise` split
found in every cell). Resolving one means choosing a vocabulary and rewriting
every caller of the others, which a round that re-reads the tree and makes a few
edits cannot land.

10 of the 11 modules more than one child defined disagree on their exported names,
against 11 of 11 in A and 11 of 12 in C. Structural, not a sampling effect.

## The planner

B is the only cell whose planner needed a retry: `decomposition.llm.parse.error`
-> `session.plan_rejected` -> `session.resumed` -> `completed`. A and C planned
clean in one pass.

The plan's hypothesis was that LOW temperature hurts the planner (citing published
vendor floors and `arXiv:2512.12895` on low-temperature looping), so the only
observed planning failure landing at the TOP of the swept range points the other
way. **It cost nothing**: B recovered on the retry and produced the best-scoring
tree of the three. One draw settles neither direction.

## Cost

30,166,347 tokens against the manifest's declared ~14M for a cap-1 cell, 115%
over. Breakdown:

| phase | tokens | share |
| --- | ---: | ---: |
| plan | 110,341 | 0.4% |
| leaves (8) | 9,028,918 | 30.0% |
| merge (3 attempts + 3 reviews) | **20,916,747** | **69.6%** |

**The merge is 70% of the bill.** Its 20,916,747 lands within 2% of cell C's
21,309,323 for the same three attempts, on a different plan and a different tree,
so merge cost is essentially a function of attempt count: roughly 9M for the first
attempt and ~6M for each retry.

Input outweighs output 10.8 to 1 (27,618,228 against 2,548,119 over 722 calls),
because every file the merge reads is re-sent on every later turn. Cost is
quadratic in the length of the read phase, so neither a higher `max_tokens` nor a
larger budget reduces it; shortening what the merge must read before it can start
is the only lever, which is the argument for a contract stage stated as money.
