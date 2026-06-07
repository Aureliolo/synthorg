# module-kind: declarative
"""Persistence event constants for the artifact sub-domain."""

from typing import Final

PERSISTENCE_ARTIFACT_SAVED: Final[str] = "persistence.artifact.saved"
PERSISTENCE_ARTIFACT_SAVE_FAILED: Final[str] = "persistence.artifact.save_failed"
PERSISTENCE_ARTIFACT_FETCHED: Final[str] = "persistence.artifact.fetched"
PERSISTENCE_ARTIFACT_FETCH_FAILED: Final[str] = "persistence.artifact.fetch_failed"
PERSISTENCE_ARTIFACT_LISTED: Final[str] = "persistence.artifact.listed"
PERSISTENCE_ARTIFACT_LIST_FAILED: Final[str] = "persistence.artifact.list_failed"
PERSISTENCE_ARTIFACT_DELETED: Final[str] = "persistence.artifact.deleted"
PERSISTENCE_ARTIFACT_DELETE_FAILED: Final[str] = "persistence.artifact.delete_failed"
PERSISTENCE_ARTIFACT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.artifact.deserialize_failed"
)
PERSISTENCE_ARTIFACT_METADATA_MISSING: Final[str] = (
    "persistence.artifact.metadata_missing"
)
PERSISTENCE_ARTIFACT_DELETE_NO_STORAGE: Final[str] = (
    "persistence.artifact.delete_no_storage"
)
