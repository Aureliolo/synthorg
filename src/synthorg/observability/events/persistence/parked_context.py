# module-kind: declarative
"""Persistence event constants for the parked_context sub-domain."""

from typing import Final

PERSISTENCE_PARKED_CONTEXT_SAVED: Final[str] = "persistence.parked_context.saved"
PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED: Final[str] = (
    "persistence.parked_context.save_failed"
)
PERSISTENCE_PARKED_CONTEXT_QUERIED: Final[str] = "persistence.parked_context.queried"
PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED: Final[str] = (
    "persistence.parked_context.query_failed"
)
PERSISTENCE_PARKED_CONTEXT_NOT_FOUND: Final[str] = (
    "persistence.parked_context.not_found"
)
PERSISTENCE_PARKED_CONTEXT_DELETED: Final[str] = "persistence.parked_context.deleted"
PERSISTENCE_PARKED_CONTEXT_DELETE_FAILED: Final[str] = (
    "persistence.parked_context.delete_failed"
)
PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.parked_context.deserialize_failed"
)
