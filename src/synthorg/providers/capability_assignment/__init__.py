"""Best-effort, operator-visible model-tier assignment for stakes routing."""

from synthorg.providers.capability_assignment.classifier import (
    CapabilityClassification,
    HeuristicTierClassifier,
    ModelCapabilityClassifier,
    classify_model_capability,
)
from synthorg.providers.capability_assignment.errors import (
    TierClassifierDisabledError,
    TierClassifierModelUnsetError,
    TierClassifierProviderUnavailableError,
    TierOverrideStoreReadOnlyError,
)
from synthorg.providers.capability_assignment.llm_recommender import LlmTierRecommender
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityOverride,
    CapabilityOverrideMap,
    CapabilityProvenance,
    CapabilityRecommendation,
)
from synthorg.providers.capability_assignment.service import (
    CapabilityAssignmentService,
    TierOverrideStore,
)

__all__ = [
    "CapabilityAssignment",
    "CapabilityAssignmentService",
    "CapabilityClassification",
    "CapabilityOverride",
    "CapabilityOverrideMap",
    "CapabilityProvenance",
    "CapabilityRecommendation",
    "HeuristicTierClassifier",
    "LlmTierRecommender",
    "ModelCapabilityClassifier",
    "TierClassifierDisabledError",
    "TierClassifierModelUnsetError",
    "TierClassifierProviderUnavailableError",
    "TierOverrideStore",
    "TierOverrideStoreReadOnlyError",
    "classify_model_capability",
]
