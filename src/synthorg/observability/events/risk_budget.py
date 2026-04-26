"""Risk budget event constants."""

from typing import Final

# Risk scorer events
RISK_BUDGET_SCORER_CREATED: Final[str] = "risk_budget.scorer.created"
RISK_BUDGET_SCORE_COMPUTED: Final[str] = "risk_budget.score.computed"
RISK_BUDGET_SCORE_FALLBACK: Final[str] = "risk_budget.score.fallback"

# Risk tracker events
RISK_BUDGET_TRACKER_CREATED: Final[str] = "risk_budget.tracker.created"
RISK_BUDGET_RECORD_ADDED: Final[str] = "risk_budget.record.added"
RISK_BUDGET_RECORD_FAILED: Final[str] = "risk_budget.record.failed"
RISK_BUDGET_AGENT_QUERIED: Final[str] = "risk_budget.agent.queried"
RISK_BUDGET_TASK_QUERIED: Final[str] = "risk_budget.task.queried"
RISK_BUDGET_TOTAL_QUERIED: Final[str] = "risk_budget.total.queried"
RISK_BUDGET_RECORDS_QUERIED: Final[str] = "risk_budget.records.queried"

# Risk tracker eviction events
RISK_BUDGET_RECORDS_PRUNED: Final[str] = "risk_budget.records.pruned"
RISK_BUDGET_RECORDS_AUTO_PRUNED: Final[str] = "risk_budget.records.auto_pruned"

# Risk enforcement events
RISK_BUDGET_ENFORCEMENT_CHECK: Final[str] = "risk_budget.enforcement.check"
RISK_BUDGET_LIMIT_EXCEEDED: Final[str] = "risk_budget.limit.exceeded"
RISK_BUDGET_DAILY_LIMIT_EXCEEDED: Final[str] = "risk_budget.daily_limit.exceeded"
RISK_BUDGET_TASK_LIMIT_EXCEEDED: Final[str] = "risk_budget.task_limit.exceeded"
RISK_BUDGET_DOWNGRADE_TRIGGERED: Final[str] = "risk_budget.downgrade.triggered"

# Shadow mode events
RISK_BUDGET_SHADOW_LOGGED: Final[str] = "risk_budget.shadow.logged"
RISK_BUDGET_SHADOW_WOULD_BLOCK: Final[str] = "risk_budget.shadow.would_block"
