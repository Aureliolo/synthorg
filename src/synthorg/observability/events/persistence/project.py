# module-kind: declarative
"""Persistence event constants for the project sub-domain."""

from typing import Final

PERSISTENCE_PROJECT_SAVED: Final[str] = "persistence.project.saved"
PERSISTENCE_PROJECT_SAVE_FAILED: Final[str] = "persistence.project.save_failed"
PERSISTENCE_PROJECT_FETCHED: Final[str] = "persistence.project.fetched"
PERSISTENCE_PROJECT_FETCH_FAILED: Final[str] = "persistence.project.fetch_failed"
PERSISTENCE_PROJECT_LISTED: Final[str] = "persistence.project.listed"
PERSISTENCE_PROJECT_LIST_FAILED: Final[str] = "persistence.project.list_failed"
PERSISTENCE_PROJECT_DELETED: Final[str] = "persistence.project.deleted"
PERSISTENCE_PROJECT_DELETE_FAILED: Final[str] = "persistence.project.delete_failed"
PERSISTENCE_PROJECT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project.deserialize_failed"
)
