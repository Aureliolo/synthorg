# module-kind: feature
"""Factory for the promotion service.

Assembles :class:`PromotionService` from its default strategy trio
(threshold criteria, seniority-gated approval, seniority->model
mapping) so the boot path can construct the subsystem without knowing
each strategy's wiring. Keeping the assembly here lets the runtime
wiring stay a thin call and keeps the strategy choices in one place.
"""

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.seniority_approval_strategy import SeniorityApprovalStrategy
from synthorg.hr.promotion.seniority_model_mapping import SeniorityModelMapping
from synthorg.hr.promotion.service import (
    PromotionNotificationCallback,
    PromotionService,
)
from synthorg.hr.promotion.threshold_evaluator import ThresholdEvaluator
from synthorg.hr.registry import AgentRegistryService
from synthorg.security.trust.service import TrustService


def build_promotion_service(  # noqa: PLR0913 -- explicit DI of the promotion service collaborators
    *,
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
    config: PromotionConfig | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    trust_service: TrustService | None = None,
    on_notification: PromotionNotificationCallback | None = None,
) -> PromotionService:
    """Build a ``PromotionService`` with the default strategy trio.

    Args:
        registry: Agent registry the service reads + mutates.
        tracker: Performance tracker feeding criteria evaluation.
        config: Promotion configuration; defaults applied when ``None``.
        approval_store: Optional approval store for human-gated
            promotions; without it the service auto-applies per its
            approval strategy.
        trust_service: Optional trust service for post-promotion
            re-evaluation.
        on_notification: Optional promotion/demotion notification sink.

    Returns:
        A constructed ``PromotionService``.
    """
    resolved = config or PromotionConfig()
    return PromotionService(
        criteria_strategy=ThresholdEvaluator(config=resolved.criteria),
        approval_strategy=SeniorityApprovalStrategy(config=resolved.approval),
        model_mapping_strategy=SeniorityModelMapping(config=resolved.model_mapping),
        registry=registry,
        tracker=tracker,
        config=resolved,
        approval_store=approval_store,
        trust_service=trust_service,
        on_notification=on_notification,
    )
