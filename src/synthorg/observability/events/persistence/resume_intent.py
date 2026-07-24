# module-kind: declarative
"""Persistence event constants for the resume_intent sub-domain."""

from typing import Final

PERSISTENCE_RESUME_INTENT_SAVE_FAILED: Final[str] = (
    "persistence.resume_intent.save_failed"
)
PERSISTENCE_RESUME_INTENT_QUERIED: Final[str] = "persistence.resume_intent.queried"
PERSISTENCE_RESUME_INTENT_QUERY_FAILED: Final[str] = (
    "persistence.resume_intent.query_failed"
)
PERSISTENCE_RESUME_INTENT_NOT_FOUND: Final[str] = "persistence.resume_intent.not_found"
PERSISTENCE_RESUME_INTENT_DELETE_FAILED: Final[str] = (
    "persistence.resume_intent.delete_failed"
)
PERSISTENCE_RESUME_INTENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.resume_intent.deserialize_failed"
)
