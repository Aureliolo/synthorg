"""Best-effort, operator-visible model-tier assignment for stakes routing."""

from synthorg.providers.tier_assignment.classifier import (
    HeuristicTierClassifier,
    ModelTierClassifier,
    TierClassification,
    classify_model_tier,
)
from synthorg.providers.tier_assignment.errors import (
    TierClassifierDisabledError,
    TierClassifierModelUnsetError,
    TierClassifierProviderUnavailableError,
    TierOverrideStoreReadOnlyError,
)
from synthorg.providers.tier_assignment.llm_recommender import LlmTierRecommender
from synthorg.providers.tier_assignment.models import (
    TierAssignment,
    TierAssignmentMap,
    TierAssignmentOverride,
    TierProvenance,
    TierRecommendation,
)
from synthorg.providers.tier_assignment.service import (
    TierAssignmentService,
    TierOverrideStore,
)

__all__ = [
    "HeuristicTierClassifier",
    "LlmTierRecommender",
    "ModelTierClassifier",
    "TierAssignment",
    "TierAssignmentMap",
    "TierAssignmentOverride",
    "TierAssignmentService",
    "TierClassification",
    "TierClassifierDisabledError",
    "TierClassifierModelUnsetError",
    "TierClassifierProviderUnavailableError",
    "TierOverrideStore",
    "TierOverrideStoreReadOnlyError",
    "TierProvenance",
    "TierRecommendation",
    "classify_model_tier",
]
