# module-kind: declarative
"""Persistence event constants for the oauth_state sub-domain (transient flow state)."""

from typing import Final

PERSISTENCE_OAUTH_STATE_SAVED: Final[str] = "persistence.oauth_state.saved"
PERSISTENCE_OAUTH_STATE_SAVE_FAILED: Final[str] = "persistence.oauth_state.save_failed"
PERSISTENCE_OAUTH_STATE_FETCHED: Final[str] = "persistence.oauth_state.fetched"
PERSISTENCE_OAUTH_STATE_FETCH_FAILED: Final[str] = (
    "persistence.oauth_state.fetch_failed"
)
PERSISTENCE_OAUTH_STATE_DELETED: Final[str] = "persistence.oauth_state.deleted"
PERSISTENCE_OAUTH_STATE_DELETE_FAILED: Final[str] = (
    "persistence.oauth_state.delete_failed"
)
PERSISTENCE_OAUTH_STATE_CLEANUP: Final[str] = "persistence.oauth_state.cleanup"
PERSISTENCE_OAUTH_STATE_CLEANUP_FAILED: Final[str] = (
    "persistence.oauth_state.cleanup_failed"
)
PERSISTENCE_OAUTH_STATE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.oauth_state.deserialize_failed"
)
