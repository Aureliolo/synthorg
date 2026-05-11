"""Budget guard for K-candidate trajectory sampling.

Prevents trajectory scoring from exceeding the remaining task
budget.  When the budget is insufficient for K candidates, the
caller falls back to single-candidate execution.
"""

from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.trajectory import (
    TRAJECTORY_BUDGET_GUARD_BLOCKED,
)

logger = get_logger(__name__)


_DEFAULT_MARGIN: Final[float] = 0.2


def check_trajectory_budget(
    *,
    remaining_budget: float,
    estimated_step_cost: float,
    k: int,
    margin: float = _DEFAULT_MARGIN,
) -> bool:
    """Check if K-candidate sampling fits within the budget.

    Args:
        remaining_budget: Remaining task budget in base currency.
        estimated_step_cost: Estimated cost of a single step.
        k: Number of candidates to sample.
        margin: Fraction of remaining budget to reserve (0.0--1.0).

    Returns:
        ``True`` if budget allows K candidates, ``False`` otherwise.
    """
    if k < 1:
        logger.warning(
            TRAJECTORY_BUDGET_GUARD_BLOCKED,
            k=k,
            reason="k must be at least 1",
        )
        return False
    if not 0.0 <= margin <= 1.0:
        logger.warning(
            TRAJECTORY_BUDGET_GUARD_BLOCKED,
            margin=margin,
            reason="margin must be in [0.0, 1.0]",
        )
        return False
    if remaining_budget <= 0 or estimated_step_cost <= 0:
        logger.info(
            TRAJECTORY_BUDGET_GUARD_BLOCKED,
            remaining_budget=remaining_budget,
            estimated_step_cost=estimated_step_cost,
            k=k,
            reason="non-positive budget or step cost",
        )
        return False

    available = remaining_budget * (1.0 - margin)
    required = estimated_step_cost * k

    if required > available:
        logger.info(
            TRAJECTORY_BUDGET_GUARD_BLOCKED,
            remaining_budget=remaining_budget,
            available_after_margin=available,
            required=required,
            k=k,
            margin=margin,
            reason="insufficient budget for K candidates",
        )
        return False

    return True
