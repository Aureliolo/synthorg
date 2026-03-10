"""Approval timeout event constants."""

from typing import Final

TIMEOUT_POLICY_EVALUATED: Final[str] = "timeout.policy.evaluated"
TIMEOUT_AUTO_APPROVED: Final[str] = "timeout.auto_approved"
TIMEOUT_AUTO_DENIED: Final[str] = "timeout.auto_denied"
TIMEOUT_ESCALATED: Final[str] = "timeout.escalated"
TIMEOUT_WAITING: Final[str] = "timeout.waiting"
