"""Promotion and demotion subsystem.

Provides pluggable strategies for evaluating, approving, and applying
agent seniority level changes with model mapping support.
"""

from synthorg.hr.promotion.approval_protocol import PromotionApprovalStrategy
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.criteria_protocol import PromotionCriteriaStrategy
from synthorg.hr.promotion.model_mapping_protocol import ModelMappingStrategy
from synthorg.hr.promotion.models import (
    CriterionResult,
    PromotionApprovalDecision,
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)
from synthorg.hr.promotion.service import PromotionService

__all__ = [
    "CriterionResult",
    "ModelMappingStrategy",
    "PromotionApprovalDecision",
    "PromotionApprovalStrategy",
    "PromotionConfig",
    "PromotionCriteriaStrategy",
    "PromotionEvaluation",
    "PromotionRecord",
    "PromotionRequest",
    "PromotionService",
]
