"""Domain models for the self-improving company meta-loop.

Aggregating facade. The model definitions live in topic modules:
enums in ``_model_enums``, rollback + altitude-specific change payloads
in ``_change_models``, the improvement proposal in ``_proposal_models``,
and the signal-match / guard / outcome records in ``_result_models``;
the org-signal snapshot models live in ``signal_models``. Every name
stays importable from ``synthorg.meta.models`` via ``__all__`` (this
module is imported from ``api/`` and ``persistence/``).
"""

from synthorg.meta._change_models import (
    ArchitectureChange,
    CodeChange,
    ConfigChange,
    PromptChange,
    RollbackOperation,
    RollbackPlan,
)
from synthorg.meta._model_enums import (
    CodeOperation,
    EvolutionMode,
    GuardVerdict,
    ProposalAltitude,
    ProposalStatus,
    RegressionVerdict,
    RolloutOutcome,
    RolloutStrategyType,
    RuleSeverity,
)
from synthorg.meta._proposal_models import (
    ImprovementProposal,
    ProposalRationale,
)
from synthorg.meta._result_models import (
    ApplyResult,
    CIValidationResult,
    GuardResult,
    ImprovementCycleResult,
    RegressionResult,
    RegressionThresholds,
    RolloutResult,
    RuleMatch,
)
from synthorg.meta.signal_models import (
    ErrorCategorySummary,
    EvolutionOutcomeSummary,
    MetricSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ScalingDecisionSummary,
    TrendDirection,
)

__all__ = [
    # Core models
    "ApplyResult",
    "ArchitectureChange",
    "CIValidationResult",
    "CodeChange",
    # Enums
    "CodeOperation",
    "ConfigChange",
    # Re-exported signal models
    "ErrorCategorySummary",
    "EvolutionMode",
    "EvolutionOutcomeSummary",
    "GuardResult",
    "GuardVerdict",
    "ImprovementCycleResult",
    "ImprovementProposal",
    "MetricSummary",
    "OrgBudgetSummary",
    "OrgCoordinationSummary",
    "OrgErrorSummary",
    "OrgEvolutionSummary",
    "OrgPerformanceSummary",
    "OrgScalingSummary",
    "OrgSignalSnapshot",
    "OrgTelemetrySummary",
    "PromptChange",
    "ProposalAltitude",
    "ProposalRationale",
    "ProposalStatus",
    "RegressionResult",
    "RegressionThresholds",
    "RegressionVerdict",
    "RollbackOperation",
    "RollbackPlan",
    "RolloutOutcome",
    "RolloutResult",
    "RolloutStrategyType",
    "RuleMatch",
    "RuleSeverity",
    "ScalingDecisionSummary",
    "TrendDirection",
]
