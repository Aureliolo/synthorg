"""Autonomy subsystem event constants."""

from typing import Final

# autonomy.promotion.requested / denied / downgrade.triggered /
# recovery.requested moved to events.security as
# SECURITY_AUTONOMY_* (audit-chained).
AUTONOMY_RESOLVED: Final[str] = "autonomy.resolved"
AUTONOMY_SENIORITY_VIOLATION: Final[str] = "autonomy.seniority.violation"
AUTONOMY_PRESET_EXPANDED: Final[str] = "autonomy.preset.expanded"
AUTONOMY_ACTION_AUTO_APPROVED: Final[str] = "autonomy.action.auto_approved"
AUTONOMY_ACTION_HUMAN_REQUIRED: Final[str] = "autonomy.action.human_required"
