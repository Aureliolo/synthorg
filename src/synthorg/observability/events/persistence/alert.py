"""Persistence event constants for the durable org-alert log.

Constants follow the ``persistence.alert.<action>`` naming
convention and are passed as the first argument to structured log calls.
"""

from typing import Final

PERSISTENCE_ALERT_SAVE_FAILED: Final[str] = "persistence.alert.save_failed"
PERSISTENCE_ALERT_QUERIED: Final[str] = "persistence.alert.queried"
PERSISTENCE_ALERT_QUERY_FAILED: Final[str] = "persistence.alert.query_failed"
PERSISTENCE_ALERT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.alert.deserialize_failed"
)
