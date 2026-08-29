#!/usr/bin/env python3
"""Pre-push / CI gate: every unfinished plan has something that can move it.

A plan's waves are driven by a background task started when an operator
approves the plan. That is an edge, and an edge does not survive the process
that took it, so after a restart nothing anywhere was left asking whether a
dispatched plan still needed driving. A live run ended with two subtasks at
``in_progress``, one at ``in_review`` and a plan at ``executing``, and the
board went on showing work in flight with nothing behind it. Restarting is an
ordinary operator action, so this was not an exotic failure: it was what every
restart did, silently.

``RunRecoveryReconciler`` is the level-triggered answer, and this gate holds it
to the property that makes it one: it must have an answer for EVERY plan
status. A status the reconciler does not name is a status nothing watches,
which is the defect rather than a gap in it, and the compiler cannot see it
because the classification is a set of frozensets rather than a match
statement.

Two directions, both of them failures:

- **Uncovered**: a status in no group. A new status added next year defaults
  to unwatched, which is the wrong default, so the gate refuses it.
- **Double-claimed**: a status in two groups. Two owners for one decision is
  the sibling defect, where the quieter one wins and nobody is told which.

There is deliberately no baseline and no per-line opt-out. An exception is a
line added to one of the declarations, in the open, where the next reader can
see which answer that status gets.

Usage::

    python scripts/check_run_recovery_covers_plan_statuses.py
"""

import argparse
import sys
from collections.abc import Iterator, Mapping

from synthorg.core.plan_enums import STAGE_STATUSES, TERMINAL_STATUSES, PlanStatus
from synthorg.engine.run_recovery.reconciler import (
    AWAITING_HUMAN_STATUSES,
    DRIVEN_STATUSES,
    UNFILLED_STATUSES,
)

#: Each group with the answer it gives, so a violation says what the status
#: would have got rather than only which set it is missing from.
#:
#: The stage group covers head and tail together because the recovery question
#: is the same for both: a stage owns its own advance off a derived task id, so
#: one recompute re-drives it. Splitting them here would let a status sit in the
#: head set while the reconciler only reads the tail one, which is precisely the
#: silent-gap shape this gate exists to refuse.
_GROUPS: Mapping[str, frozenset[PlanStatus]] = {
    "terminal (nothing left to do)": TERMINAL_STATUSES,
    "awaiting a human (parked correctly)": AWAITING_HUMAN_STATUSES,
    "unfillable (failed with a reason)": UNFILLED_STATUSES,
    "driven (waves handed to the coordinator)": DRIVEN_STATUSES,
    "stage (one rollup pass re-drives the stage)": STAGE_STATUSES,
}


def _violations() -> Iterator[str]:
    """Yield one line per status with no owner, or with two.

    Yields:
        A human-readable violation line.
    """
    for status in PlanStatus:
        owners = [name for name, group in _GROUPS.items() if status in group]
        if not owners:
            yield (
                f"PlanStatus.{status.name} is in no run-recovery group, so "
                "nothing would ever move a plan holding it. Add it to the "
                "group naming what it should get, in "
                "src/synthorg/engine/run_recovery/reconciler.py."
            )
        elif len(owners) > 1:
            joined = " AND ".join(owners)
            yield (
                f"PlanStatus.{status.name} is claimed by two run-recovery "
                f"groups ({joined}), so which one acts depends on statement "
                "order rather than on a decision anybody wrote down."
            )


def main() -> int:
    """Run the gate.

    Returns:
        ``0`` when every plan status has exactly one owner, else ``1``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    found = list(_violations())
    if not found:
        print(
            f"OK: all {len(PlanStatus)} plan statuses have exactly one "
            "run-recovery owner"
        )
        return 0
    print("Run-recovery coverage gate failed:\n")
    for line in found:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
