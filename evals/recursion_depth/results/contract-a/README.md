# Every leaf here reads undelivered, and none of them failed

This recording is kept for the defect it caught, not for the numbers on its
face. **Do not read its `delivered` column.** It was recorded before the
own-test gate knew what a contract does to a tree, and under that gate no leaf
in this arm could have delivered whatever it built.

## What happened

The contract stage writes one FAILING test per specification requirement, and
each leaf is briefed to make the tests covering its own requirements pass and
to **leave the others failing**, because they belong to units that have not run
yet. That brief is right and the leaves followed it.

The delivery gate then ran the WHOLE suite in the leaf's tree and required it
green. So every leaf was judged on 42 requirements' worth of tests, 37 of which
it had been instructed not to touch.

Measured against `control-a`, which differs only by the contract stage:

| | leaves finished | delivered | files changed |
|---|---:|---:|---|
| `contract-a` (this) | 3 | **0** | 0, 2, 5 |
| `control-a` | 6 | **6** | 3–10 |

Nothing about the work explains that gap. The gate does.

## Why it was not obvious

The two arms disagree only where a tree carries tests the unit did not write,
which is only true under a contract. In the control arm the leaf's checkout
holds its own suite and nothing else, so "run everything" and "run what this
unit owns" are the same question and the gate had never been wrong before.

It also fails in the direction that looks like a result: a contract arm
delivering nothing reads as the treatment failing, which is a finding somebody
would have written up.

## The fix

A leaf under a contract is graded on the tests naming the requirements it
claims (`execute.py::_delivery`, `grading.py::selection_args`, joined on the
requirement id the contract is told to name each test for). Three states, not
two: `None` where no contract seeded the tree and the whole suite is the unit's
own work, a non-empty selection where it owns tests, and the empty tuple where
a contract seeded the tree and this unit claims no requirement, which decides
nothing and says so rather than falling back to the whole suite.

## What this recording IS still evidence for

The flow, which the delivery gate does not touch. Read it with
`scripts/report_session_flow.py --by-run`: against four non-contract
recordings its leaves take a third of the turns, carry a third of the context,
and spend 36% of their calls reading rather than 3–5%. That is the contract
being read instead of an interface being invented, and it is the mechanism the
arm was built to test.

Its interface divergence is also real: 0 of 21 shared modules diverge here,
against 2 of 3 in `control-a`.
