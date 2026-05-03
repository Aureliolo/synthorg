"""Budget event constants."""

from typing import Final

BUDGET_TRACKER_CREATED: Final[str] = "budget.tracker.created"
BUDGET_TRACKER_CLEARED: Final[str] = "budget.tracker.cleared"
BUDGET_RECORD_ADDED: Final[str] = "budget.record.added"
BUDGET_RECORD_DEDUPED: Final[str] = "budget.record.deduped"
BUDGET_SUMMARY_BUILT: Final[str] = "budget.summary.built"
BUDGET_TOTAL_COST_QUERIED: Final[str] = "budget.total_cost.queried"
BUDGET_AGENT_COST_QUERIED: Final[str] = "budget.agent_cost.queried"
BUDGET_TIME_RANGE_INVALID: Final[str] = "budget.time_range.invalid"
BUDGET_MIXED_CURRENCY_REJECTED: Final[str] = "budget.mixed_currency.rejected"
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
BUDGET_TIER_PRESERVED: Final[str] = "budget.tier.preserved"

BUDGET_RECORDS_QUERIED: Final[str] = "budget.records.queried"

BUDGET_UTILIZATION_QUERIED: Final[str] = "budget.utilization.queried"
BUDGET_UTILIZATION_ERROR: Final[str] = "budget.utilization.error"

# Cost tracker eviction events
BUDGET_RECORDS_PRUNED: Final[str] = "budget.records.pruned"
BUDGET_RECORDS_AUTO_PRUNED: Final[str] = "budget.records.auto_pruned"
BUDGET_QUERY_EXCEEDS_RETENTION: Final[str] = "budget.query.exceeds_retention"

BUDGET_PROVIDER_USAGE_QUERIED: Final[str] = "budget.provider_usage.queried"
BUDGET_NOTIFICATION_FAILED: Final[str] = "budget.notification.failed"

# Embedding cost tracking events
BUDGET_EMBEDDING_COST_RECORDED: Final[str] = "budget.embedding_cost.recorded"
BUDGET_EMBEDDING_COST_FAILED: Final[str] = "budget.embedding_cost.failed"
BUDGET_EMBEDDING_MODEL_UNPRICED: Final[str] = "budget.embedding_cost.model_unpriced"

# Project-level budget events
BUDGET_PROJECT_COST_QUERIED: Final[str] = "budget.project_cost.queried"
BUDGET_PROJECT_RECORDS_QUERIED: Final[str] = "budget.project_records.queried"
BUDGET_PROJECT_BUDGET_EXCEEDED: Final[str] = "budget.project_budget.exceeded"
BUDGET_PROJECT_ENFORCEMENT_CHECK: Final[str] = "budget.project.enforcement_check"

# Durable project cost aggregate events
BUDGET_PROJECT_COST_AGGREGATED: Final[str] = "budget.project_cost.aggregated"
BUDGET_PROJECT_COST_AGGREGATION_FAILED: Final[str] = (
    "budget.project_cost.aggregation_failed"
)
BUDGET_PROJECT_BASELINE_SOURCE: Final[str] = "budget.project_baseline.source"

# Cost-recording chokepoint background-task drain
BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED: Final[str] = (
    "budget.pending_record.drain_unexpected"
)
