# The cells that proved the merge verdict was wrong

Four cells: three at cap 1, one at cap 2, abandoned when the contrast between
them turned out to be a harness defect rather than a depth effect. Kept because
that contrast IS the evidence, and no single cell shows it.

| cap | rep | leaves | passed | sessions |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0 | 8 | 35 of 42 | 15 |
| 1 | 1 | 8 | 37 of 42 | 15 |
| 1 | 2 | 8 | 38 of 42 | 15 |
| 2 | 0 | 39 | **0 of 42** | 64 |

Read naively this is a strong result: depth 1 works, depth 2 collapses. It is
not a result at all.

## What was actually happening

A merge declared two expected artifacts, its own report and its end-to-end
output, and `produced_nothing` decided delivery by asking whether any DECLARED
path had changed. A merge that assembled the entire package and skipped the
markdown file was therefore recorded as having changed nothing.

Both root merges here wrote a real package (12 modules at cap 1, 7 at cap 2)
and neither wrote its report, so both were marked undelivered.

That verdict does not stay local. `merge_brief` annotates each child with
`[DID NOT DELIVER]` for its parent. The cap-2 root merge was briefed:

```
- .children/01-sql-lexer-and-recursive-descent-parser-p/: ...  [DID NOT DELIVER]
- .children/02-query-planner-semantic-validation-agains/: ...  [DID NOT DELIVER]
- .children/05-joins-executor-inner-join-left-join-qual/: ...  [DID NOT DELIVER]
```

Every one of those subtrees had assembled a package. The root was told most of
its inputs were broken and behaved accordingly.

## Why it looked exactly like a depth effect

The defect can only fire BELOW the root. A cap-1 tree has no intermediate
merges, so no false label ever reaches the root and the oracle sees a real
assembly: 35, 37, 38. A cap-2 tree has one per subtree, all false, and the root
is briefed that its inputs failed: 0.

A bug whose severity scales with tree depth is indistinguishable, from the
curve alone, from depth not working. Had the matrix run to completion it would
have reported that recursion collapses beyond depth 1, with twelve cells and
twenty hours of spend behind the claim.

## What the cap-1 cells still say

They are sound on their own terms and worth reading: three draws of the same
cap on independently planned trees, scoring 35, 37 and 38 of 42. That is a
tight spread, and it is the first evidence in this harness that a cap-1 cell is
reproducible rather than a coin flip. Compare `../pre-transcript-fix/`, where
the same cap scored 0 because its eight leaves happened not to compose, and
`../sandbox-teardown-race/`, where a single leaf built the whole package alone
and scored 38.

## The fix

Delivery is now judged on the assembled tree: whether anything changed at the
workspace root outside `.children/`, the merge's own `.synthorg/` paperwork and
the README it started from. The report stays required for the human record and
stops deciding the verdict.
