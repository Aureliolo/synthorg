# module-kind: declarative
"""Persistence event constants for the checkpoint sub-domain."""

from typing import Final

PERSISTENCE_CHECKPOINT_SAVED: Final[str] = "persistence.checkpoint.saved"
PERSISTENCE_CHECKPOINT_SAVE_FAILED: Final[str] = "persistence.checkpoint.save_failed"
PERSISTENCE_CHECKPOINT_QUERIED: Final[str] = "persistence.checkpoint.queried"
PERSISTENCE_CHECKPOINT_QUERY_FAILED: Final[str] = "persistence.checkpoint.query_failed"
PERSISTENCE_CHECKPOINT_NOT_FOUND: Final[str] = "persistence.checkpoint.not_found"
PERSISTENCE_CHECKPOINT_DELETED: Final[str] = "persistence.checkpoint.deleted"
PERSISTENCE_CHECKPOINT_DELETE_FAILED: Final[str] = (
    "persistence.checkpoint.delete_failed"
)
PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.checkpoint.deserialize_failed"
)
