# module-kind: declarative
"""Persistence event constants for the hiring_request sub-domain."""

from typing import Final

PERSISTENCE_HIRING_REQUEST_SAVED: Final[str] = "persistence.hiring_request.saved"
PERSISTENCE_HIRING_REQUEST_SAVE_FAILED: Final[str] = (
    "persistence.hiring_request.save_failed"
)
PERSISTENCE_HIRING_REQUEST_QUERIED: Final[str] = "persistence.hiring_request.queried"
PERSISTENCE_HIRING_REQUEST_QUERY_FAILED: Final[str] = (
    "persistence.hiring_request.query_failed"
)
PERSISTENCE_HIRING_REQUEST_DESERIALIZE_FAILED: Final[str] = (
    "persistence.hiring_request.deserialize_failed"
)
