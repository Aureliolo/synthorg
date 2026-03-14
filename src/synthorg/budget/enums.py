"""Budget-specific enumerations."""

from enum import StrEnum


class BudgetAlertLevel(StrEnum):
    """Alert severity levels for budget thresholds.

    Used by :class:`~synthorg.budget.spending_summary.SpendingSummary`
    to indicate the current budget state relative to configured thresholds.
    """

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    HARD_STOP = "hard_stop"
