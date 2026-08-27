# The first cell with a working sandbox

One cap-1 gated cell, recorded after the sandbox-ownership fix and abandoned
when the transcript recorder turned out to have the same shared-state race.
Kept because it is a sound measurement in its own right: the sandbox held for
every leaf, and this is the first cell in this harness where all eight leaves
were given a working shell.

It cannot be resumed into. The transcript fix moved the commit, and the journal
identity pins it.

## What it measured

| | value |
| --- | ---: |
| achieved depth | 1 |
| leaves | 8 |
| leaves delivered | **6 of 8** |
| requirements passed | **0 of 42** |
| sessions | 15 |
| tokens | ~14.7M |

Per unit:

| unit | turns | tokens | delivered | why not |
| --- | ---: | ---: | --- | --- |
| plan | 0 | 68,085 | n/a | |
| SQL lexer | 25 | 466,568 | yes | |
| CSV reader with header and integer typing | 46 | 779,798 | yes | |
| Semantic analyser and plan validation | 32 | 626,622 | yes | |
| Reader: floats, NULL, and quoted fields | 47 | 979,886 | yes | |
| Output formatters | 31 | 640,594 | yes | |
| SQL parser and AST | 62 | 1,528,034 | no | the suite collected no tests |
| CLI entry point and integration tests | 48 | 1,507,381 | yes | |
| Execution engine | 55 | 1,511,392 | no | the suite collected no tests |
| merge | 80 | 6,578,419 | no | no assembly attempt changed anything the node declared |

## What it says

**The bottleneck is assembly, not delivery.** Six leaves built and tested their
own units. The merge then spent 6.58M tokens over three attempts, changed
nothing the node declared, and the held-out oracle passed nothing at all.

The two leaves that failed did so honestly: both hit the 1.5M token ceiling
with code written and no tests collected, which is what a cap-1 unit looks like
when one leaf carries a whole subsystem. That is consistent with the
raised-ceiling experiment under `../ceiling-3m/`, which found cap 1 fails
structurally rather than for want of budget.

Read against `../sandbox-teardown-race/`, which is the same cap and the same
pair with the sandbox defect present: there, one leaf of eight delivered and a
single leaf that happened to build the whole package alone scored 38 of 42. The
contrast is worth keeping. A tree assembled from one leaf's work scored higher
than a tree assembled from six, which says nothing good about the merge.

## Why it was abandoned

`TranscriptRecorder` held one bound path for a recorder shared by every
concurrent session. Three of the eight leaves produced no transcript at all, and
one file named for a single leaf held requests from four different units. The
curves do not depend on transcripts, but the record of WHY does, and that record
is what identified the sandbox defect in the first place. Depth 4 has never been
measured, so the run was stopped at its cheapest cell and restarted with the
recorder keyed per session.
