"""Promotion and demotion subsystem.

Provides pluggable strategies for evaluating, approving, and applying
agent seniority level changes with model mapping support.
"""

from ai_company.hr.promotion.approval_protocol import PromotionApprovalStrategy
from ai_company.hr.promotion.config import PromotionConfig
from ai_company.hr.promotion.criteria_protocol import PromotionCriteriaStrategy
from ai_company.hr.promotion.model_mapping_protocol import ModelMappingStrategy
from ai_company.hr.promotion.models import (
    CriterionResult,
    PromotionApprovalDecision,
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)
from ai_company.hr.promotion.service import PromotionService

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
