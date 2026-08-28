# The cell that scored zero because tests were in the wrong directory

Four cells, three at cap 1 and one at cap 2, abandoned when the cap-2 cell
scored zero and the reason turned out to be where its sub-merges had left
their test files.

| cap | rep | leaves | passed | sessions | tokens |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0 | 8 | 37 of 42 | 10 | 12.2M |
| 1 | 1 | 6 | 37 of 42 | 8 | 12.8M |
| 1 | 2 | 8 | 23 of 42 | 10 | 17.8M |
| 2 | 0 | 44 | **0 of 42** | 43 | 44.7M |

Read naively this is the same strong result the previous abandoned run
offered: depth 1 works, depth 2 collapses. It is again not a result.

## The mechanism

`grading.py` runs a unit's own suite from its workspace root:

```
python -m pytest -c .synthorg-grade.ini -p no:cacheprovider -q \
    --junit-xml=.synthorg-grade.xml .
```

The inputs to a merge are mounted at `.children/<slug>/`. That directory is
dot-prefixed, and pytest's default `norecursedirs` includes `.*`, so pytest
never descends into it. A merge that assembles the package to the workspace
root and leaves the pieces' tests where it found them therefore collects **no
tests at all**, whatever it built.

The correlation across the cap-2 cell's seven merges is exact:

| tests at the root of the merge | tests still under `.children/` | verdict |
| ---: | ---: | --- |
| 9 | 10 | delivered |
| 1 | 10 | delivered |
| 0 | 8 | undelivered |
| 0 | 7 | undelivered |
| 0 | 3 | undelivered |
| 0 | 7 | undelivered |

Nothing in the merge brief asked for the tests to be brought up, and the
verdict depended on it.

## Why it reached the score

`MergeOutcome.delivered` answered two questions at once: did this assemble
anything, and does what it assembled stand up. `merge_brief` rendered the
single flag as `[DID NOT DELIVER]` in the parent's brief.

So the root merge was told four of its seven pieces had delivered nothing.
Those four held **46, 46, 41, and 36 Python modules**. In total it was handed
277 modules and 61 test files across seven subtrees, and told most of it had
failed.

It then ran six attempts and 119 turns and wrote no file at all. The oracle
graded an empty tree: 0 of 42.

## Why it looks exactly like a depth effect, again

The mark can only mislead where there is a parent to mislead. A cap-1 tree has
one merge and nothing above it, so its verdict is briefed to nobody and the
oracle grades what the merge actually built: 37, 37, 23. A cap-2 tree has six
sub-merges whose verdicts all reach the root.

That is the second defect in this harness whose severity scales with the
treatment axis, after `../merge-delivery-false-negative/`. A curve built from
these cells would have reported that recursion collapses beyond depth 1, twice,
for two unrelated reasons, and both times the number would have looked clean.

## What the cap-1 cells still say

They stand on their own terms: three independently planned trees at the same
cap, scoring 37, 37, and 23. The spread is what three repetitions were bought
for, and the 23 is a real draw rather than an artefact.

## The fix

Three changes, all in this directory's parent package.

`UnitDelivery` splits the flag. `produced` says whether the unit's own tree
changed and is the half the parent's brief renders; `reason` says what is wrong
with what it built. A piece that built a package and failed a check is now
briefed as `[BUILT, BUT NOT SIGNED OFF: <reason>]` rather than as having
delivered nothing, and a merge is told in as many words to assemble it and fix
it rather than write it again.

The brief also stopped contradicting itself. Its prose asserted that every
piece "has passed its own tests in its own tree" while the list underneath
marked four of seven as failures.

And it now says where the tests have to live, which its own verdict depended on
and which nothing had ever stated.
