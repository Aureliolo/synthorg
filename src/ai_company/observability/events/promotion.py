"""Promotion event constants for structured logging.

Constants follow the ``promotion.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

PROMOTION_EVALUATE_START: Final[str] = "promotion.evaluate.start"
PROMOTION_EVALUATE_COMPLETE: Final[str] = "promotion.evaluate.complete"
PROMOTION_EVALUATE_FAILED: Final[str] = "promotion.evaluate.failed"
PROMOTION_REQUESTED: Final[str] = "promotion.requested"
PROMOTION_APPROVAL_SUBMITTED: Final[str] = "promotion.approval.submitted"
PROMOTION_REJECTED: Final[str] = "promotion.rejected"
PROMOTION_APPLIED: Final[str] = "promotion.applied"
PROMOTION_COOLDOWN_ACTIVE: Final[str] = "promotion.cooldown.active"
PROMOTION_APPROVAL_DECIDED: Final[str] = "promotion.approval.decided"
DEMOTION_APPLIED: Final[str] = "promotion.demotion.applied"
PROMOTION_MODEL_CHANGED: Final[str] = "promotion.model.changed"
PROMOTION_NOTIFICATION_SENT: Final[str] = "promotion.notification.sent"
