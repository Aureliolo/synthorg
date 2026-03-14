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
BUDGET_HARD_STOP_EXCEEDED: Final[str] = "budget.hard_stop.exceeded"
BUDGET_HARD_STOP_TRIGGERED: Final[str] = "budget.hard_stop.triggered"
BUDGET_DAILY_LIMIT_EXCEEDED: Final[str] = "budget.daily_limit.exceeded"
BUDGET_DOWNGRADE_APPLIED: Final[str] = "budget.downgrade.applied"
BUDGET_DOWNGRADE_SKIPPED: Final[str] = "budget.downgrade.skipped"
BUDGET_ENFORCEMENT_CHECK: Final[str] = "budget.enforcement.check"
BUDGET_TASK_LIMIT_HIT: Final[str] = "budget.task_limit.hit"
BUDGET_DAILY_LIMIT_HIT: Final[str] = "budget.daily_limit.hit"
BUDGET_BASELINE_ERROR: Final[str] = "budget.baseline.error"
BUDGET_PREFLIGHT_ERROR: Final[str] = "budget.preflight.error"
BUDGET_RESOLVE_MODEL_ERROR: Final[str] = "budget.resolve_model.error"

BUDGET_TIER_RESOLVED: Final[str] = "budget.tier.resolved"
BUDGET_TIER_CLASSIFY_MISS: Final[str] = "budget.tier.classify_miss"

BUDGET_RECORDS_QUERIED: Final[str] = "budget.records.queried"
