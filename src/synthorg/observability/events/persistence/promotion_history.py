# module-kind: declarative
"""Persistence event constants for the promotion_history sub-domain."""

from typing import Final

PERSISTENCE_PROMOTION_HISTORY_APPENDED: Final[str] = (
    "persistence.promotion_history.appended"
)
PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED: Final[str] = (
    "persistence.promotion_history.append_failed"
)
PERSISTENCE_PROMOTION_HISTORY_QUERIED: Final[str] = (
    "persistence.promotion_history.queried"
)
PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED: Final[str] = (
    "persistence.promotion_history.query_failed"
)
PERSISTENCE_PROMOTION_HISTORY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.promotion_history.deserialize_failed"
)
