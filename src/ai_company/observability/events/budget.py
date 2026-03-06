"""Budget lifecycle event constants."""

from typing import Final

BUDGET_TRACKER_CREATED: Final[str] = "budget.tracker.created"
BUDGET_RECORD_ADDED: Final[str] = "budget.record.added"
BUDGET_SUMMARY_BUILT: Final[str] = "budget.summary.built"
BUDGET_TOTAL_COST_QUERIED: Final[str] = "budget.total_cost.queried"
BUDGET_AGENT_COST_QUERIED: Final[str] = "budget.agent_cost.queried"
BUDGET_TIME_RANGE_INVALID: Final[str] = "budget.time_range.invalid"
BUDGET_DEPARTMENT_RESOLVE_FAILED: Final[str] = "budget.department.resolve_failed"
