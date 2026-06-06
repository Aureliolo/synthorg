# module-kind: declarative
"""Persistence event constants for the artifact_storage sub-domain."""

from typing import Final

PERSISTENCE_ARTIFACT_STORED: Final[str] = "persistence.artifact_storage.stored"
PERSISTENCE_ARTIFACT_STORE_FAILED: Final[str] = (
    "persistence.artifact_storage.store_failed"
)
PERSISTENCE_ARTIFACT_RETRIEVED: Final[str] = "persistence.artifact_storage.retrieved"
PERSISTENCE_ARTIFACT_RETRIEVE_FAILED: Final[str] = (
    "persistence.artifact_storage.retrieve_failed"
)
PERSISTENCE_ARTIFACT_STORAGE_DELETED: Final[str] = (
    "persistence.artifact_storage.deleted"
)
PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED: Final[str] = (
    "persistence.artifact_storage.delete_failed"
)
PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED: Final[str] = (
    "persistence.artifact_storage.rollback_failed"
)
PERSISTENCE_ARTIFACT_CONTENT_MISSING: Final[str] = (
    "persistence.artifact_storage.content_missing"
)
