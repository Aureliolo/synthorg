# module-kind: declarative
"""Persistence event constants for the lifecycle_event sub-domain."""

from typing import Final

PERSISTENCE_LIFECYCLE_EVENT_SAVED: Final[str] = "persistence.lifecycle_event.saved"
PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED: Final[str] = (
    "persistence.lifecycle_event.save_failed"
)
PERSISTENCE_LIFECYCLE_EVENT_LISTED: Final[str] = "persistence.lifecycle_event.listed"
PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED: Final[str] = (
    "persistence.lifecycle_event.list_failed"
)
PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.lifecycle_event.deserialize_failed"
)
