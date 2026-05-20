"""Process-fact penalty constants and lookup table.

A brief's run emits structured events while it executes. Some of those
events are *process facts*: signals that the run misbehaved in a way
the scorer should subtract from the brief's grade. The constants below
encode the per-event deduction; the :class:`PenaltyTable` resolves an
event name to a (penalty class, points) pair so the aggregator can
report a breakdown without coupling to the underlying event constants.

Values are weighted by severity, not by frequency. A single hard-stop
(``budget.hard_stop.exceeded``) outweighs a 75% threshold cross because
the former means the run actually halted; the latter is an early
warning the company chose to push through. ``PENALTY_CAP_PER_CLASS``
prevents one noisy event class from zeroing a brief on its own.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability.events.approval_gate import APPROVAL_GATE_REVIEW_REWORK
from synthorg.observability.events.budget import (
    BUDGET_DAILY_LIMIT_EXCEEDED,
    BUDGET_HARD_STOP_EXCEEDED,
    BUDGET_PROJECT_BUDGET_EXCEEDED,
)
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_TRANSITION_FAILED,
    EXECUTION_ENGINE_TIMEOUT,
    EXECUTION_LOOP_BUDGET_EXHAUSTED,
    EXECUTION_MAX_TURNS_EXCEEDED,
    EXECUTION_PLAN_REPLAN_EXHAUSTED,
)
from synthorg.observability.events.stagnation import (
    STAGNATION_DETECTED,
    STAGNATION_TERMINATED,
)
from synthorg.observability.events.workflow_definition import (
    SUBWORKFLOW_CYCLE_DETECTED,
)

# Budget classes -- the run cost money it should not have spent.
PENALTY_BUDGET_HARD_STOP: Final[int] = 30
PENALTY_BUDGET_DAILY_LIMIT: Final[int] = 20
PENALTY_BUDGET_PROJECT_OVER: Final[int] = 20

# Execution / time -- the run hit a turn or wall-clock ceiling.
PENALTY_MAX_TURNS_EXCEEDED: Final[int] = 15
PENALTY_LOOP_BUDGET_EXHAUSTED: Final[int] = 15
PENALTY_ENGINE_TIMEOUT: Final[int] = 25
PENALTY_BRIEF_WALL_CLOCK_OVER: Final[int] = 20

# Loops / stagnation -- the run repeated itself or replanned to death.
PENALTY_STAGNATION_DETECTED: Final[int] = 10
PENALTY_STAGNATION_TERMINATED: Final[int] = 25
PENALTY_REPLAN_EXHAUSTED: Final[int] = 10
PENALTY_SUBWORKFLOW_CYCLE: Final[int] = 10

# Governance -- the run violated a transition or required rework.
PENALTY_CONTEXT_TRANSITION_FAILED: Final[int] = 15
PENALTY_APPROVAL_REVIEW_REWORK: Final[int] = 15

# One penalty class cannot zero a brief on its own; the cap forces the
# scorer to attribute the gap to multiple classes before reaching the
# floor. Tuned so two distinct severe classes can still floor the brief
# (e.g. budget hard stop + engine timeout).
PENALTY_CAP_PER_CLASS: Final[int] = 40

# Scores never go negative; deductions below zero are clamped here.
PENALTY_FLOOR: Final[int] = 0

# Synthetic class name for wall-clock breaches measured by the runner
# itself (not an in-process event). Lives alongside the constant-derived
# class names so the aggregator treats it uniformly.
PENALTY_CLASS_BRIEF_WALL_CLOCK: Final[str] = "evals.brief.wall_clock_over"


class PenaltyTable(BaseModel):
    """Maps an event-constant string to a penalty class + point cost.

    The "penalty class" is the event-constant string itself; this keeps
    the scorecard's per-class breakdown readable without inventing a
    second taxonomy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    points_per_event: dict[str, int] = Field(default_factory=dict)
    cap_per_class: int = Field(default=PENALTY_CAP_PER_CLASS, ge=0)
    floor: int = Field(default=PENALTY_FLOOR, ge=0)

    def points_for(self, event_constant: str) -> int:
        """Return the per-event penalty for *event_constant*, or 0 if untracked."""
        return self.points_per_event.get(event_constant, 0)

    def is_tracked(self, event_constant: str) -> bool:
        """Whether *event_constant* contributes to the scorer."""
        return event_constant in self.points_per_event


DEFAULT_PENALTY_TABLE: Final[PenaltyTable] = PenaltyTable(
    points_per_event={
        BUDGET_HARD_STOP_EXCEEDED: PENALTY_BUDGET_HARD_STOP,
        BUDGET_DAILY_LIMIT_EXCEEDED: PENALTY_BUDGET_DAILY_LIMIT,
        BUDGET_PROJECT_BUDGET_EXCEEDED: PENALTY_BUDGET_PROJECT_OVER,
        EXECUTION_MAX_TURNS_EXCEEDED: PENALTY_MAX_TURNS_EXCEEDED,
        EXECUTION_LOOP_BUDGET_EXHAUSTED: PENALTY_LOOP_BUDGET_EXHAUSTED,
        EXECUTION_ENGINE_TIMEOUT: PENALTY_ENGINE_TIMEOUT,
        STAGNATION_DETECTED: PENALTY_STAGNATION_DETECTED,
        STAGNATION_TERMINATED: PENALTY_STAGNATION_TERMINATED,
        EXECUTION_PLAN_REPLAN_EXHAUSTED: PENALTY_REPLAN_EXHAUSTED,
        SUBWORKFLOW_CYCLE_DETECTED: PENALTY_SUBWORKFLOW_CYCLE,
        EXECUTION_CONTEXT_TRANSITION_FAILED: PENALTY_CONTEXT_TRANSITION_FAILED,
        APPROVAL_GATE_REVIEW_REWORK: PENALTY_APPROVAL_REVIEW_REWORK,
        PENALTY_CLASS_BRIEF_WALL_CLOCK: PENALTY_BRIEF_WALL_CLOCK_OVER,
    },
    cap_per_class=PENALTY_CAP_PER_CLASS,
    floor=PENALTY_FLOOR,
)


__all__ = [
    "DEFAULT_PENALTY_TABLE",
    "PENALTY_APPROVAL_REVIEW_REWORK",
    "PENALTY_BRIEF_WALL_CLOCK_OVER",
    "PENALTY_BUDGET_DAILY_LIMIT",
    "PENALTY_BUDGET_HARD_STOP",
    "PENALTY_BUDGET_PROJECT_OVER",
    "PENALTY_CAP_PER_CLASS",
    "PENALTY_CLASS_BRIEF_WALL_CLOCK",
    "PENALTY_CONTEXT_TRANSITION_FAILED",
    "PENALTY_ENGINE_TIMEOUT",
    "PENALTY_FLOOR",
    "PENALTY_LOOP_BUDGET_EXHAUSTED",
    "PENALTY_MAX_TURNS_EXCEEDED",
    "PENALTY_REPLAN_EXHAUSTED",
    "PENALTY_STAGNATION_DETECTED",
    "PENALTY_STAGNATION_TERMINATED",
    "PENALTY_SUBWORKFLOW_CYCLE",
    "PenaltyTable",
]
