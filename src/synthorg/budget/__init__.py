"""Budget and cost tracking domain models.

This module provides the domain models for budget configuration, cost
tracking, budget hierarchy, and spending summaries as described in
the Operations design page.
"""

from synthorg.budget.billing import billing_period_start, daily_period_start
from synthorg.budget.call_category import LLMCallCategory, OrchestrationAlertLevel
from synthorg.budget.category_analytics import CategoryBreakdown, OrchestrationRatio
from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from synthorg.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    ErrorCategory,
    ErrorTaxonomyConfig,
    OrchestrationAlertThresholds,
)
from synthorg.budget.coordination_metrics import (
    CoordinationEfficiency,
    CoordinationMetrics,
    CoordinationOverhead,
    ErrorAmplification,
    MessageDensity,
    RedundancyRate,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.cost_tiers import (
    BUILTIN_TIERS,
    CostTierDefinition,
    CostTiersConfig,
    classify_model_tier,
    resolve_tiers,
)
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    QuotaExhaustedError,
)
from synthorg.budget.hierarchy import (
    BudgetHierarchy,
    DepartmentBudget,
    TeamBudget,
)
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.optimizer_models import (
    AgentEfficiency,
    AnomalyDetectionResult,
    AnomalySeverity,
    AnomalyType,
    ApprovalDecision,
    CostOptimizerConfig,
    DowngradeAnalysis,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    RoutingOptimizationAnalysis,
    RoutingSuggestion,
    SpendingAnomaly,
)
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    ProviderCostModel,
    QuotaCheckResult,
    QuotaLimit,
    QuotaSnapshot,
    QuotaWindow,
    SubscriptionConfig,
    effective_cost_per_1k,
)
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.reports import (
    ModelDistribution,
    PeriodComparison,
    ProviderDistribution,
    ReportGenerator,
    SpendingReport,
    TaskSpending,
)
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from synthorg.budget.tracker import CostTracker

__all__ = [
    "BUILTIN_TIERS",
    "AgentEfficiency",
    "AgentSpending",
    "AnomalyDetectionResult",
    "AnomalySeverity",
    "AnomalyType",
    "ApprovalDecision",
    "AutoDowngradeConfig",
    "BudgetAlertConfig",
    "BudgetAlertLevel",
    "BudgetConfig",
    "BudgetEnforcer",
    "BudgetExhaustedError",
    "BudgetHierarchy",
    "CategoryBreakdown",
    "CoordinationEfficiency",
    "CoordinationMetricName",
    "CoordinationMetrics",
    "CoordinationMetricsConfig",
    "CoordinationOverhead",
    "CostOptimizer",
    "CostOptimizerConfig",
    "CostRecord",
    "CostTierDefinition",
    "CostTiersConfig",
    "CostTracker",
    "DailyLimitExceededError",
    "DegradationAction",
    "DegradationConfig",
    "DepartmentBudget",
    "DepartmentSpending",
    "DowngradeAnalysis",
    "DowngradeRecommendation",
    "EfficiencyAnalysis",
    "EfficiencyRating",
    "ErrorAmplification",
    "ErrorCategory",
    "ErrorTaxonomyConfig",
    "LLMCallCategory",
    "MessageDensity",
    "ModelDistribution",
    "OrchestrationAlertLevel",
    "OrchestrationAlertThresholds",
    "OrchestrationRatio",
    "PeriodComparison",
    "PeriodSpending",
    "ProviderCostModel",
    "ProviderDistribution",
    "QuotaCheckResult",
    "QuotaExhaustedError",
    "QuotaLimit",
    "QuotaSnapshot",
    "QuotaTracker",
    "QuotaWindow",
    "RedundancyRate",
    "ReportGenerator",
    "RoutingOptimizationAnalysis",
    "RoutingSuggestion",
    "SpendingAnomaly",
    "SpendingReport",
    "SpendingSummary",
    "SubscriptionConfig",
    "TaskSpending",
    "TeamBudget",
    "billing_period_start",
    "classify_model_tier",
    "daily_period_start",
    "effective_cost_per_1k",
    "resolve_tiers",
]
