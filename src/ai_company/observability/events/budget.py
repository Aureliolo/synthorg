"""Budget event constants."""

from typing import Final

BUDGET_TRACKER_CREATED: Final[str] = "budget.tracker.created"
BUDGET_RECORD_ADDED: Final[str] = "budget.record.added"
BUDGET_SUMMARY_BUILT: Final[str] = "budget.summary.built"
BUDGET_TOTAL_COST_QUERIED: Final[str] = "budget.total_cost.queried"
BUDGET_AGENT_COST_QUERIED: Final[str] = "budget.agent_cost.queried"
BUDGET_TIME_RANGE_INVALID: Final[str] = "budget.time_range.invalid"
BUDGET_DEPARTMENT_RESOLVE_FAILED: Final[str] = "budget.department.resolve_failed"

BUDGET_CATEGORY_BREAKDOWN_QUERIED: Final[str] = "budget.category_breakdown.queried"
BUDGET_ORCHESTRATION_RATIO_QUERIED: Final[str] = "budget.orchestration_ratio.queried"
BUDGET_ORCHESTRATION_RATIO_ALERT: Final[str] = "budget.orchestration_ratio.alert"

BUDGET_ALERT_THRESHOLD_CROSSED: Final[str] = "budget.alert.threshold_crossed"
BUDGET_HARD_STOP_TRIGGERED: Final[str] = "budget.hard_stop.triggered"
BUDGET_DAILY_LIMIT_EXCEEDED: Final[str] = "budget.daily_limit.exceeded"
BUDGET_DOWNGRADE_APPLIED: Final[str] = "budget.downgrade.applied"
BUDGET_DOWNGRADE_SKIPPED: Final[str] = "budget.downgrade.skipped"
BUDGET_ENFORCEMENT_CHECK: Final[str] = "budget.enforcement.check"
