"""Best-effort, operator-visible model-capability assignment for stakes routing."""

from synthorg.providers.capability_assignment.classifier import (
    CapabilityClassification,
    HeuristicCapabilityClassifier,
    ModelCapabilityClassifier,
    classify_model_capability,
)
from synthorg.providers.capability_assignment.errors import (
    CapabilityClassifierDisabledError,
    CapabilityClassifierModelUnsetError,
    CapabilityClassifierProviderUnavailableError,
    CapabilityOverrideStoreReadOnlyError,
)
from synthorg.providers.capability_assignment.llm_recommender import (
    LlmCapabilityRecommender,
)
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityOverride,
    CapabilityOverrideMap,
    CapabilityProvenance,
    CapabilityRecommendation,
)
from synthorg.providers.capability_assignment.service import (
    CapabilityAssignmentService,
    CapabilityOverrideStore,
)

__all__ = [
    "CapabilityAssignment",
    "CapabilityAssignmentService",
    "CapabilityClassification",
    "CapabilityClassifierDisabledError",
    "CapabilityClassifierModelUnsetError",
    "CapabilityClassifierProviderUnavailableError",
    "CapabilityOverride",
    "CapabilityOverrideMap",
    "CapabilityOverrideStore",
    "CapabilityOverrideStoreReadOnlyError",
    "CapabilityProvenance",
    "CapabilityRecommendation",
    "HeuristicCapabilityClassifier",
    "LlmCapabilityRecommender",
    "ModelCapabilityClassifier",
    "classify_model_capability",
]
