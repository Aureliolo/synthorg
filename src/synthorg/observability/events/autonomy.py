"""Autonomy subsystem event constants."""

from typing import Final

AUTONOMY_RESOLVED: Final[str] = "autonomy.resolved"
AUTONOMY_PROMOTION_REQUESTED: Final[str] = "autonomy.promotion.requested"
AUTONOMY_PROMOTION_DENIED: Final[str] = "autonomy.promotion.denied"
AUTONOMY_DOWNGRADE_TRIGGERED: Final[str] = "autonomy.downgrade.triggered"
AUTONOMY_RECOVERY_REQUESTED: Final[str] = "autonomy.recovery.requested"
AUTONOMY_SENIORITY_VIOLATION: Final[str] = "autonomy.seniority.violation"
AUTONOMY_PRESET_EXPANDED: Final[str] = "autonomy.preset.expanded"
AUTONOMY_ACTION_AUTO_APPROVED: Final[str] = "autonomy.action.auto_approved"
AUTONOMY_ACTION_HUMAN_REQUIRED: Final[str] = "autonomy.action.human_required"
