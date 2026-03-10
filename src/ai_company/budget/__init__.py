"""Budget and cost tracking domain models.

This module provides the domain models for budget configuration, cost
tracking, budget hierarchy, and spending summaries as described in
DESIGN_SPEC Section 10.
"""

from ai_company.budget.billing import billing_period_start, daily_period_start
from ai_company.budget.call_category import LLMCallCategory, OrchestrationAlertLevel
from ai_company.budget.category_analytics import CategoryBreakdown, OrchestrationRatio
from ai_company.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from ai_company.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    ErrorCategory,
    ErrorTaxonomyConfig,
    OrchestrationAlertThresholds,
)
from ai_company.budget.coordination_metrics import (
    CoordinationEfficiency,
    CoordinationMetrics,
    CoordinationOverhead,
    ErrorAmplification,
    MessageDensity,
    RedundancyRate,
)
from ai_company.budget.cost_record import CostRecord
from ai_company.budget.cost_tiers import (
    BUILTIN_TIERS,
    CostTierDefinition,
    CostTiersConfig,
    classify_model_tier,
    resolve_tiers,
)
from ai_company.budget.enforcer import BudgetEnforcer
from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    QuotaExhaustedError,
)
from ai_company.budget.hierarchy import (
    BudgetHierarchy,
    DepartmentBudget,
    TeamBudget,
)
from ai_company.budget.optimizer import CostOptimizer
from ai_company.budget.optimizer_models import (
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
from ai_company.budget.quota import (
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
from ai_company.budget.quota_tracker import QuotaTracker
from ai_company.budget.reports import (
    ModelDistribution,
    PeriodComparison,
    ProviderDistribution,
    ReportGenerator,
    SpendingReport,
    TaskSpending,
)
from ai_company.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from ai_company.budget.tracker import CostTracker

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
