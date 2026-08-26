# module-kind: code
"""Which child-planning failures the level above answers, and which it does not.

A level that asked for a child already holds a valid plan. Two child failures
leave that plan usable, so the unit dispatches carrying a reason instead of
discarding every level already paid for; everything else is a fault the plan
above cannot describe, and it propagates.

The two are the same shape reached by different routes. The planner could not
divide the unit, or the unit's planning session outran the per-session
wall-clock ceiling. Both mean this subtree was not planned and nothing about
the tree above it is wrong.

Everything else stays a raise. A transport that kept mangling replies is fixed
at the provider, and filing it as a note on one plan item hides an outage. So
is a bare ``TimeoutError``, which is what reaches this decision when the
WHOLE-TREE scope expired rather than one session's: that bound covers every
level, so no level holds a plan that outlived it. It is not a
``DecompositionError`` at all and never reaches here.

Absorbing a per-session ceiling buys no unbounded time. The tree's session
budget still caps how many ceilings one tree can pay, and the whole-tree
ceiling still ends it.
"""

from typing import Final

from synthorg.engine.decomposition.atomicity import (
    PLANNER_DECLINED,
    SESSION_BUDGET_BACKSTOP,
    SESSION_CEILING_BACKSTOP,
    STAGNATION_BACKSTOP,
    TURN_BUDGET_BACKSTOP,
    AtomicityAssessment,
    unsplit_reason,
)
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionSessionBudgetError,
    DecompositionStagnationError,
    DecompositionTimeoutError,
    DecompositionTurnBudgetError,
    DecompositionUnsplittableError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_CHILD_BUDGET_ABSORBED,
    DECOMPOSITION_CHILD_CEILING_ABSORBED,
    DECOMPOSITION_PLANNER_DECLINED,
)

logger = get_logger(__name__)

#: The backstop phrase and the event each absorbable failure is recorded under.
#: Keyed on the type because the type is what carries the distinction: the two
#: remedies differ, so a reader is sent to the bound they can move (raise the
#: per-session ceiling) or to the one they cannot (narrow the objective).
_ABSORBED: Final[dict[type[DecompositionError], tuple[str, str]]] = {
    DecompositionUnsplittableError: (PLANNER_DECLINED, DECOMPOSITION_PLANNER_DECLINED),
    DecompositionTimeoutError: (
        SESSION_CEILING_BACKSTOP,
        DECOMPOSITION_CHILD_CEILING_ABSORBED,
    ),
    DecompositionTurnBudgetError: (
        TURN_BUDGET_BACKSTOP,
        DECOMPOSITION_CHILD_BUDGET_ABSORBED,
    ),
    DecompositionSessionBudgetError: (
        SESSION_BUDGET_BACKSTOP,
        DECOMPOSITION_CHILD_BUDGET_ABSORBED,
    ),
    DecompositionStagnationError: (
        STAGNATION_BACKSTOP,
        DECOMPOSITION_CHILD_BUDGET_ABSORBED,
    ),
}


def absorbed_child_reason(
    exc: DecompositionError,
    *,
    assessment: AtomicityAssessment,
    task_id: str,
    subtask_id: str,
    current_depth: int,
) -> str | None:
    """Answer the reason to record for *exc*, or ``None`` to re-raise it.

    Args:
        exc: What the child's planning raised.
        assessment: The verdict that judged the unit oversized, which supplies
            the rule and both numbers the reason quotes.
        task_id: The child task, for the log line.
        subtask_id: The unit the reason lands on, for the log line.
        current_depth: The level that asked, for the log line.

    Returns:
        The reason to record on the unit when this level can answer the
        failure, or ``None`` when it cannot and the caller must re-raise.
    """
    absorbed = _ABSORBED.get(type(exc))
    if absorbed is None:
        return None
    backstop, event = absorbed
    logger.warning(
        event,
        task_id=task_id,
        subtask_id=subtask_id,
        current_depth=current_depth,
        error=safe_error_description(exc),
    )
    return unsplit_reason(assessment, backstop=backstop)


__all__ = ["absorbed_child_reason"]
