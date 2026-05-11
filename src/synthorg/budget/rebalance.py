"""Budget rebalancing utilities for department budget allocation.

Provides pure functions for computing how department budgets should be
adjusted when new departments are added (e.g. via template pack
application).
"""

from enum import StrEnum
from typing import Any, Final, NamedTuple

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger

logger = get_logger(__name__)


class RebalanceMode(StrEnum):
    """Strategy for adjusting department budgets on pack application."""

    NONE = "none"
    SCALE_EXISTING = "scale_existing"
    REJECT_IF_OVER = "reject_if_over"


class RebalanceResult(NamedTuple):
    """Outcome of a budget rebalance computation.

    Attributes:
        departments: Full department list (modified existing + new appended).
        old_total: Sum of existing department budgets before rebalance.
        new_total: Sum of all department budgets after rebalance.
        scale_factor: Multiplier applied to existing departments
            (``None`` when mode is not ``SCALE_EXISTING``).
        rejected: ``True`` only when mode is ``REJECT_IF_OVER`` and
            the projected total exceeds ``max_budget``.
    """

    departments: tuple[dict[str, Any], ...]
    old_total: float
    new_total: float
    scale_factor: float | None
    rejected: bool


_DEFAULT_MAX_BUDGET: Final[float] = 100.0


def compute_rebalance(
    existing_depts: list[dict[str, Any]],
    new_depts: list[dict[str, Any]],
    mode: RebalanceMode,
    *,
    max_budget: float = _DEFAULT_MAX_BUDGET,
    rounding_precision: int = BUDGET_ROUNDING_PRECISION,
) -> RebalanceResult:
    """Compute rebalanced department budgets after adding new departments.

    This is a pure function with no side effects -- it receives
    department dicts and a mode, and returns the adjusted list.

    Args:
        existing_depts: Current department dicts (each must have a
            ``budget_percent`` key).
        new_depts: Departments being added (each must have a
            ``budget_percent`` key).
        mode: Rebalance strategy to apply.
        max_budget: Maximum allowed budget total (default 100.0).
        rounding_precision: Decimal places for rounding comparisons.

    Returns:
        A :class:`RebalanceResult` with the combined department list
        and metadata about the rebalance operation.
    """
    existing_total = sum(d.get("budget_percent", 0.0) for d in existing_depts)
    new_total = sum(d.get("budget_percent", 0.0) for d in new_depts)
    combined_total = existing_total + new_total

    if mode == RebalanceMode.NONE:
        all_depts = [*existing_depts, *new_depts]
        return RebalanceResult(
            departments=tuple(all_depts),
            old_total=existing_total,
            new_total=round(combined_total, rounding_precision),
            scale_factor=None,
            rejected=False,
        )

    if mode == RebalanceMode.REJECT_IF_OVER:
        exceeds = round(combined_total, rounding_precision) > max_budget
        all_depts = [*existing_depts, *new_depts]
        return RebalanceResult(
            departments=tuple(all_depts),
            old_total=existing_total,
            new_total=round(combined_total, rounding_precision),
            scale_factor=None,
            rejected=exceeds,
        )

    # SCALE_EXISTING mode
    if round(combined_total, rounding_precision) <= max_budget:
        # No scaling needed -- already within budget.
        all_depts = [*existing_depts, *new_depts]
        return RebalanceResult(
            departments=tuple(all_depts),
            old_total=existing_total,
            new_total=round(combined_total, rounding_precision),
            scale_factor=1.0,
            rejected=False,
        )

    # Need to scale existing departments down.
    target_existing = max_budget - new_total
    if existing_total <= 0:
        # Cannot scale zero-budget departments; clamp to 0.
        factor = 0.0
    else:
        factor = max(0.0, min(1.0, target_existing / existing_total))

    scaled_existing = []
    for dept in existing_depts:
        scaled = {**dept}
        old_pct = dept.get("budget_percent", 0.0)
        scaled["budget_percent"] = round(
            old_pct * factor,
            rounding_precision,
        )
        scaled_existing.append(scaled)

    all_depts = [*scaled_existing, *new_depts]
    final_total = sum(d.get("budget_percent", 0.0) for d in all_depts)

    return RebalanceResult(
        departments=tuple(all_depts),
        old_total=existing_total,
        new_total=round(final_total, rounding_precision),
        scale_factor=round(factor, rounding_precision),
        rejected=False,
    )
