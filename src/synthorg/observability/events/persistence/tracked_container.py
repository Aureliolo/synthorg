# module-kind: declarative
"""Persistence event constants for the tracked_container sub-domain (sandboxes)."""

from typing import Final

PERSISTENCE_TRACKED_CONTAINER_SAVED: Final[str] = "persistence.tracked_container.saved"
PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED: Final[str] = (
    "persistence.tracked_container.save_failed"
)
PERSISTENCE_TRACKED_CONTAINER_LOADED: Final[str] = (
    "persistence.tracked_container.loaded"
)
PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED: Final[str] = (
    "persistence.tracked_container.load_failed"
)
PERSISTENCE_TRACKED_CONTAINER_DELETED: Final[str] = (
    "persistence.tracked_container.deleted"
)
PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED: Final[str] = (
    "persistence.tracked_container.delete_failed"
)
