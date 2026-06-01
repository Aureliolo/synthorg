# module-kind: code
"""Runner-local penalty table extending the spine's default.

The benchmark runner measures one process fact the in-process event stream
cannot emit: a run whose total cost blew the company's per-run budget ceiling.
This is the runner's analogue of the spine's wall-clock breach, so it is
modelled as an extra synthetic penalty class layered onto
``DEFAULT_PENALTY_TABLE`` here (the scoring spine stays untouched).
"""

from typing import Final

from evals.scoring.penalties import DEFAULT_PENALTY_TABLE, PenaltyTable

# Synthetic class for a run whose measured cost blew the company's per-run hard
# ceiling. Observed by the runner (comparing the run's total cost to the
# operator-configured ceiling), not an in-process event, so an under-budgeted
# (broken) company is attributed a concrete process fact, not only a lower grade.
PENALTY_CLASS_BRIEF_BUDGET_OVER: Final[str] = "evals.brief.budget_over"
_PENALTY_BRIEF_BUDGET_OVER: Final[int] = 30

BENCHMARK_PENALTY_TABLE: Final[PenaltyTable] = PenaltyTable(
    points_per_event={
        **DEFAULT_PENALTY_TABLE.points_per_event,
        PENALTY_CLASS_BRIEF_BUDGET_OVER: _PENALTY_BRIEF_BUDGET_OVER,
    },
    cap_per_class=DEFAULT_PENALTY_TABLE.cap_per_class,
    floor=DEFAULT_PENALTY_TABLE.floor,
)


__all__ = ["BENCHMARK_PENALTY_TABLE", "PENALTY_CLASS_BRIEF_BUDGET_OVER"]
