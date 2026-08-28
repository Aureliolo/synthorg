# The raised-ceiling cell

One cap-1 cell, recorded at `unit_token_ceiling: 3000000` and abandoned after
it. It is kept because it is the only measurement of what raising that ceiling
does, and it is the evidence behind the paragraph the manifest now carries.

It has no report of its own: the run was stopped after the first cell, so there
is nothing to plot. `cells.jsonl` and `progress.jsonl` are what it wrote.

## What it measured

Three cap-1 cells exist across this harness. The ceiling is the variable that
moved, not the model:

| run | executor | ceiling | leaves | sessions | turns | tokens | leaves delivered | oracle |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| pilot | minimax-m3 | 1.5M | 7 | 14 | 254 | 6.68M | n/a | 0/42 |
| smoke | glm-5.3-flash | 1.5M | 8 | 15 | 474 | 16.41M | 2 of 8 | 0/42 |
| this | minimax-m3 | **3M** | 8 | 15 | **976** | **32.52M** | **0 of 8** | 0/42 |

Per leaf, the executor went from 0.95M tokens to 4.07M: **4.3x the cost from a
2x raise**, and delivered fewer leaves than the same tree did on half the
budget.

## Why it went the wrong way

The setting bounds TOKENS while a leaf spends TURNS. Raising it does not let a
stuck leaf finish; it lets a stuck leaf keep going, which is why turns went from
254 to 976 while the graded result did not move at all.

That fits what the sweep already knew about cap 1: it fails structurally rather
than for want of budget, because no recursion is allowed there and one leaf
carries a whole subsystem. The argument for the raise was a smoke leaf that had
run 112
turns with five tests still failing when it was cut, read as converging. It was
not converging.

## What this does not say

Cap 1 is the shallowest cell and the one where units are largest, so a raised
ceiling could still help at depth, where a leaf is one function rather than a
subsystem. Nothing here tests that. What it establishes is that the raise is
expensive and that cap 1 does not repay it.

## Reading it back

The journal is readable on its own terms, but a report cannot be re-scored from
it against the current manifest: the manifest digest changed when the ceiling
was reverted, and the header pins it.
