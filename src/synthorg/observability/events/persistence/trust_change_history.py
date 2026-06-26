# module-kind: declarative
"""Persistence event constants for the trust_change_history sub-domain."""

from typing import Final

PERSISTENCE_TRUST_CHANGE_HISTORY_APPENDED: Final[str] = (
    "persistence.trust_change_history.appended"
)
PERSISTENCE_TRUST_CHANGE_HISTORY_APPEND_FAILED: Final[str] = (
    "persistence.trust_change_history.append_failed"
)
PERSISTENCE_TRUST_CHANGE_HISTORY_QUERIED: Final[str] = (
    "persistence.trust_change_history.queried"
)
PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED: Final[str] = (
    "persistence.trust_change_history.query_failed"
)
PERSISTENCE_TRUST_CHANGE_HISTORY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.trust_change_history.deserialize_failed"
)
