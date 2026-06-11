# module-kind: declarative
"""Persistence event constants for the conflict-escalation queue sub-domain."""

from typing import Final

PERSISTENCE_ESCALATION_DESERIALIZE_FAILED: Final[str] = (
    "persistence.escalation.deserialize_failed"
)
PERSISTENCE_ESCALATION_CREATE_FAILED: Final[str] = (
    "persistence.escalation.create_failed"
)
PERSISTENCE_ESCALATION_GET_FAILED: Final[str] = "persistence.escalation.get_failed"
PERSISTENCE_ESCALATION_LIST_FAILED: Final[str] = "persistence.escalation.list_failed"
PERSISTENCE_ESCALATION_MARK_EXPIRED_FAILED: Final[str] = (
    "persistence.escalation.mark_expired_failed"
)
PERSISTENCE_ESCALATION_UPDATE_FAILED: Final[str] = (
    "persistence.escalation.update_failed"
)
PERSISTENCE_ESCALATION_NOTIFY_FAILED: Final[str] = (
    "persistence.escalation.notify_failed"
)
PERSISTENCE_ESCALATION_SUBSCRIBE_FAILED: Final[str] = (
    "persistence.escalation.subscribe_failed"
)
