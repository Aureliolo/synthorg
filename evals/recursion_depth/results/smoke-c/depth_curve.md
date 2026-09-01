# Recursion-depth sweep

Does verification at every merge hold off aggregation collapse as
recursive decomposition deepens?

- Measured against commit `02a917c0c19b692930073c8c640e993d893fefc7` (dirty tree)
- Generated 2026-08-31T23:27:39.101288+00:00
- Manifest `sha256:f472802c810331077b3a9511c35232447f4132eba152768f0c08ed9318135bf8`
- Spec `sqlcsv`, 42 requirements
- Executor `example-provider/example-capable-001`, reviewer `example-provider/example-expert-001` (cross_family)
- Executor declared: temperature 0.6, top_p 0.95, reasoning_effort unset, max_tokens 131072
- Reviewer declared: temperature 0.6, top_p 0.95, reasoning_effort high, max_tokens 65536
- Sandbox image `ghcr.io/aureliolo/synthorg-sandbox@sha256:af8996364caca94ba07b98b593a091afe4a11208d1f8c7cbe8966b35ca700e81`
- Total spend: unpriced across 30857086 tokens (journalled)

## Specification satisfied by depth reached

What share of the specification the merged tree satisfies. Binned on
the depth each tree actually reached, not on the cap its run was
allowed: sweeping the cap does not sweep depth. This denominator is
the same for every cell and cannot empty, so every run has a point,
and it says nothing about where the work came from.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 19 | 42 | 0.452 | 1 | 15 | 30857086 | unpriced |

## Specification satisfied by depth cap

The manipulated variable, for comparison with the histogram below.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 19 | 42 | 0.452 | 1 | 15 | 30857086 | unpriced |

## Leaf-work survival by depth reached

The question the sweep was built around: of the requirements the
DELIVERED leaves claimed, how many the merged tree still satisfies.
Same axis as the curve above, so the two read together. A bucket
whose delivered leaves claimed nothing has no rate and reads `n/a`,
which is not the same as a rate of zero.

| Depth | Arm | Survived | Claimed | Fraction | Runs |
|---:|---|---:|---:|---:|---:|
| 1 | gated | 8 | 20 | 0.400 | 1 |

## Leaf-work survival by depth cap

| Depth | Arm | Survived | Claimed | Fraction | Runs |
|---:|---|---:|---:|---:|---:|
| 1 | gated | 8 | 20 | 0.400 | 1 |

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
| 1 | gated | 1 | 19..19 | 19 | 42 | 0.400..0.400 | 0.400 |

### The same, by depth cap

| Depth | Arm | Runs | Satisfied (min..max) | Median | Required | Survival (min..max) | Median |
|---:|---|---:|---|---:|---:|---|---:|
| 1 | gated | 1 | 19..19 | 19 | 42 | 0.400..0.400 | 0.400 |

## Every cell

One row per run, which is the population behind every figure above.
An unavailable cell is listed too, because it cost real money and
leaving it out would make the matrix read as smaller than it was.

| Cell | Achieved | Satisfied | Required | Sessions | Tokens | Spend |
|---|---|---:|---:|---:|---:|---:|
| d1-gated-r0 | 1 | 19 | 42 | 15 | 30857086 | unpriced |

## How deep the runs went

| Cap and depth reached | Runs |
|---|---:|
| cap=1 gated reached=1 | 1 |

## What each arm spent, and what it bought

| Arm | Merges | Sessions | Tokens | Spend | Parked escalations | Contract amendments |
|---|---:|---:|---:|---:|---:|---:|
| gated | 1 | 6 | 21309323 | unpriced | 0 | 0 |
| ungated | 0 | 0 | 0 | 0.0000 | 0 | 0 |

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
| d1-gated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no | 73 | budget_exhausted, budget_exhausted, budget_exhausted |

## Caveats

- Two curves, and they answer different questions. SPECIFICATION is the share of the specification's own requirements the merged tree satisfies: a denominator every cell shares, which cannot empty, and which says nothing about where the work came from, so a tree scoring well because the merging agent rebuilt it reads there exactly like one whose leaves survived. SURVIVAL is the share of the requirements DELIVERED leaves claimed that the merged tree still satisfies: the question this sweep was built around, on a denominator that is leaf work and can be empty, in which case the point is absent rather than zero. The two coming apart IS the finding.
- Unit sizing is the planner's own: the size signal reads the declaration a planner made, so this measures gated recursion UNDER PLANNER-DECLARED SIZING and cannot separate 'recursion fails' from 'the planner sized badly'. Separating them needs an agent that has read the code deciding its own split, which no published system has.
- The oracle is held out: it never enters a workspace and is named in no brief, so a delivery cannot be built to it.
- At least one connection this sweep dispatched through does not price its calls (its billing model is not in MEASURABLE_BILLING_MODELS), or could not be resolved at all, so every cost figure in this recording is absent rather than zero: an unpriced call and a free one are not the same claim. Token counts are unaffected and remain the figure the equal-budget check is stated in.
