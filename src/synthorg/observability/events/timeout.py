"""Approval timeout event constants."""

from typing import Final

TIMEOUT_POLICY_EVALUATED: Final[str] = "timeout.policy.evaluated"
TIMEOUT_AUTO_APPROVED: Final[str] = "timeout.auto_approved"
TIMEOUT_AUTO_DENIED: Final[str] = "timeout.auto_denied"
TIMEOUT_ESCALATED: Final[str] = "timeout.escalated"
TIMEOUT_WAITING: Final[str] = "timeout.waiting"
TIMEOUT_CONTEXT_PARKED: Final[str] = "timeout.context.parked"
TIMEOUT_CONTEXT_RESUMED: Final[str] = "timeout.context.resumed"
TIMEOUT_UNKNOWN_ACTION_TYPE: Final[str] = "timeout.unknown_action_type"
TIMEOUT_FACTORY_UNKNOWN_CONFIG: Final[str] = "timeout.factory.unknown_config"
