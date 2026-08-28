# The cell that proved the leaf verdict was wrong

One cap-1 cell, 15 sessions, 16.2M tokens, scoring 41 of 42 requirements.
Abandoned when a routine sanity pass over its units did not reconcile with the
trees on disk.

| unit | turns | Python files written | recorded as |
| --- | ---: | ---: | --- |
| SQL parser | 58 | 8 | delivered |
| CSV reader | 70 | 4 | **undelivered** |
| CLI shell and package scaffold | 2 | 0 | undelivered |
| Planner and executor | 54 | 8 | **undelivered** |
| SQL lexer | 56 | 4 | delivered |
| Integration coverage for remaining criteria | 9 | 0 | undelivered |
| Output renderers | 65 | 5 | delivered |
| End-to-end integration tests and README | 60 | 10 | **undelivered** |

Three of the eight leaves wrote 4, 8 and 10 Python modules and were recorded as
having produced nothing.

## What was actually happening

The leaf's delivery check asked whether any path the PLANNER had declared for
that unit existed and had changed. Those paths are guessed from a title and a
sentence, before the tree exists. A leaf briefed to build the CSV reader was
expected at `sqlcsv/csv_reader.py` and wrote `sqlcsv/reader.py`, so nothing it
did counted.

This is the same defect as `../merge-delivery-false-negative/` one level down,
and both are now answered by one primitive: a unit is judged on the tree it
produced, never on a guess made before it ran.

## What it cost the metric

A leaf's verdict decides whether its claimed requirements enter the SURVIVAL
denominator, so a false negative removes them from the metric rather than
scoring them zero. On this cell:

| denominator | leaves counted | survival |
| --- | ---: | --- |
| as recorded | 3 | 2 of 3 |
| as the tree says | 6 | 5 of 6 |

Both readings are healthy, which is the point worth keeping: the bias is not
visible in the ratio. It is visible in the population, and a curve built from
three claims per cell is not a curve.

## The other thing this cell shows, which is not a defect

The denominator is small for a reason that has nothing to do with the bug. The
planner allocated its requirements catastrophically unevenly: seven leaves
claimed one requirement each, and the eighth, briefed as "Integration coverage
for remaining criteria", claimed the other 35. That leaf ran nine turns, wrote
nothing at all, and took 35 of 42 requirements out of the survival denominator
with it, correctly.

That is a planner behaviour, not a harness defect, and the sweep exists to
measure planner behaviour. It is recorded here because it bounds what the
survival curve can say: when one residual bucket carries most of the
specification, survival is measured over whatever is left.

The same leaf is the case for telling a run what its workspace holds: it had
turns remaining, and nothing told it that workspace was still empty.
