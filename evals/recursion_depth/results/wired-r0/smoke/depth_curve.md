# Recursion-depth sweep

Does verification at every merge hold off aggregation collapse as
recursive decomposition deepens?

- Measured against commit `bba198159dede79b877a11a502c1a43e0f0a1ddc`
- Generated 2026-09-03T05:56:21.679804+00:00
- Manifest `sha256:3a98844285d65f3ca82a8ebeef5db9a86afde5c5924a46d6c4e22ef4b3bbf490`
- Spec `sqlcsv`, 42 requirements
- Executor `example-provider/example-capable-001`, reviewer `example-provider/example-expert-001` (cross_family)
- Executor declared: temperature 0.7, top_p 1.0, reasoning_effort high, max_tokens 131072
- Reviewer declared: temperature 0.6, top_p 0.95, reasoning_effort high, max_tokens 65536
- Sandbox image `synthorg-sandbox:local`
- Loop: contract stage on, 3 merge attempt(s)
- Total spend: unpriced across 50539928 tokens (journalled)

## Wiring, as measured on the wire

Not measured. This recording predates the one-cell smoke that
reads each treatment off the engine and the recorded request
bodies before a matrix is paid for, so what it ran under is
what its provenance ASSERTS rather than what was checked.

## Tokens per solved requirement by depth reached (headline)

What one solved requirement cost, pooled over each bucket's runs, with
a 95% percentile bootstrap interval over those runs. This is the axis
the arms are ranked on: a loop can be cheaper by an order of magnitude
at a pass rate no interval separates, so the two fraction curves below
say what was solved and this says what it cost. Two arms whose
intervals overlap at a depth cannot be ranked there, and the caveats
say so. A bucket under 3 runs reports no
interval; one that solved nothing has no finite cost per solved
requirement and reads `n/a`; an interval open above means some
resample of the bucket's runs solved nothing at all. The detectable
factor is the design's power, read off the same runs: two arms with
no effect between them differ by at least that factor one time in
twenty, so a real gap smaller than it is inside the noise and the
next arm is not worth paying for until the floor rises.

| Depth | Arm | Tokens per solved | 95% interval | Detectable factor | Tokens | Solved | Runs |
|---:|---|---:|---|---:|---:|---:|---:|
| 1 | gated | 7,219,990 | n/a | n/a | 50539928 | 7 | 1 |

## Tokens per solved requirement by depth cap

| Depth | Arm | Tokens per solved | 95% interval | Detectable factor | Tokens | Solved | Runs |
|---:|---|---:|---|---:|---:|---:|---:|
| 1 | gated | 7,219,990 | n/a | n/a | 50539928 | 7 | 1 |

## Specification satisfied by depth reached

What share of the specification the merged tree satisfies. Binned on
the depth each tree actually reached, not on the cap its run was
allowed: sweeping the cap does not sweep depth. This denominator is
the same for every cell and cannot empty, so every run has a point,
and it says nothing about where the work came from.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 7 | 42 | 0.167 | 1 | 16 | 50539928 | unpriced |

## Specification satisfied by depth cap

The manipulated variable, for comparison with the histogram below.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 7 | 42 | 0.167 | 1 | 16 | 50539928 | unpriced |

## Leaf-work survival by depth reached

The question the sweep was built around: of the requirements the
DELIVERED leaves claimed, how many the merged tree still satisfies.
Same axis as the curve above, so the two read together. A bucket
whose delivered leaves claimed nothing has no rate and reads `n/a`,
which is not the same as a rate of zero.

| Depth | Arm | Survived | Claimed | Fraction | Runs |
|---:|---|---:|---:|---:|---:|
| 1 | gated | 1 | 8 | 0.125 | 1 |

## Leaf-work survival by depth cap

| Depth | Arm | Survived | Claimed | Fraction | Runs |
|---:|---|---:|---:|---:|---:|
| 1 | gated | 1 | 8 | 0.125 | 1 |

## Per-depth spread

Both curves above POOL a bucket's repetitions into one fraction, which
is the right shape for a rate over work and cannot say whether a low
point is one bad draw or a real drop. That is the question a cap is
recorded more than once to answer, so the range and the middle run are
reported here. The middle is the LOW median, so it is always a figure
some run actually recorded rather than one describing none of them. A
survival range reads `n/a` when no run in the bucket attributed
anything, which is not the same as a rate of zero.

| Depth | Arm | Runs | Satisfied (min..max) | Median | Required | Survival (min..max) | Median |
|---:|---|---:|---|---:|---:|---|---:|
| 1 | gated | 1 | 7..7 | 7 | 42 | 0.125..0.125 | 0.125 |

### The same, by depth cap

| Depth | Arm | Runs | Satisfied (min..max) | Median | Required | Survival (min..max) | Median |
|---:|---|---:|---|---:|---:|---|---:|
| 1 | gated | 1 | 7..7 | 7 | 42 | 0.125..0.125 | 0.125 |

## Every cell

One row per run, which is the population behind every figure above.
An unavailable cell is listed too, because it cost real money and
leaving it out would make the matrix read as smaller than it was.
`Deliverable` is whether the program the specification names RUNS,
asked apart from the score beside it: a declared module imported and
a declared entry point ran without raising. It is never folded into
the score, because a tree satisfying a hidden oracle while its named
artefact is dead is exactly the disagreement the column exists to
show.

| Cell | Achieved | Satisfied | Required | Deliverable | Diverged | Sessions | Tokens | Spend |
|---|---|---:|---:|---|---:|---:|---:|---:|
| d1-gated-r0 | 1 | 7 | 42 | live | 7/27 | 16 | 50539928 | unpriced |

## How deep the runs went

| Cap and depth reached | Runs |
|---|---:|
| cap=1 gated reached=1 | 1 |

## What each arm spent, and what it bought

| Arm | Merges | Sessions | Tokens | Judging | Compacting | Spend | Parked escalations | Contract amendments |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gated | 1 | 6 | 26213785 | 2838419 | 0 | unpriced | 0 | 0 |
| ungated | 0 | 0 | 0 | 0 | 0 | unpriced | 0 | 0 |

## Who judged whom

The gate is the treatment, so a reviewer that came up on the executor's
own binding would bias the result toward the null while every
sweep-level field still read correctly. Every pairing that actually ran
is listed, with the families the decorrelation claim rests on.

| Arm | Assembled by | Judged by | Merges |
|---|---|---|---:|
| gated | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | 1 |

## Every merge

Both parties per merge, which is the grain the independence claim is
made at. The same rows are in `depth_curve.json` under each cell's
`units`.

`Attempts ended` names how each assembling session stopped. A merge
that delivered nothing because it was cut off at its budget and one
that ran freely and assembled nothing are the same row in every other
column, and only the first is a statement about the budget rather
than about the work.

| Cell | Depth | Assembly | Assembled by | Judged by | Verdict | Parked | Amendments | Delivered | Files changed | Attempts ended |
|---|---:|---|---|---|---|---|---:|---|---:|---|
| d1-gated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no | 39 | max_turns, max_turns, parked |

## Caveats

- Two curves, and they answer different questions. SPECIFICATION is the share of the specification's own requirements the merged tree satisfies: a denominator every cell shares, which cannot empty, and which says nothing about where the work came from, so a tree scoring well because the merging agent rebuilt it reads there exactly like one whose leaves survived. SURVIVAL is the share of the requirements DELIVERED leaves claimed that the merged tree still satisfies: the question this sweep was built around, on a denominator that is leaf work and can be empty, in which case the point is absent rather than zero. The two coming apart IS the finding.
- The headline figure is tokens per solved requirement, with a 95% bootstrap interval over the runs in each bucket. A loop can be cheaper by an order of magnitude at a pass rate no interval separates, so the arms are ranked on what a solved requirement COST. The SPECIFICATION and SURVIVAL curves say what was solved and where the work came from; neither ranks the arms on what it cost.
- Unit sizing is the planner's own: the size signal reads the declaration a planner made, so this measures gated recursion UNDER PLANNER-DECLARED SIZING and cannot separate 'recursion fails' from 'the planner sized badly'. Separating them needs an agent that has read the code deciding its own split, which no published system has.
- The oracle is held out: it never enters a workspace and is named in no brief, so a delivery cannot be built to it.
