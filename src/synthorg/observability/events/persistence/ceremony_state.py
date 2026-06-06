# module-kind: declarative
"""Persistence event constants for the ceremony_state sub-domain."""

from typing import Final

PERSISTENCE_CEREMONY_STATE_SAVED: Final[str] = "persistence.ceremony_state.saved"
PERSISTENCE_CEREMONY_STATE_SAVE_FAILED: Final[str] = (
    "persistence.ceremony_state.save_failed"
)
PERSISTENCE_CEREMONY_STATE_LOADED: Final[str] = "persistence.ceremony_state.loaded"
PERSISTENCE_CEREMONY_STATE_LOAD_FAILED: Final[str] = (
    "persistence.ceremony_state.load_failed"
)
PERSISTENCE_CEREMONY_STATE_DELETED: Final[str] = "persistence.ceremony_state.deleted"
PERSISTENCE_CEREMONY_STATE_DELETE_FAILED: Final[str] = (
    "persistence.ceremony_state.delete_failed"
)
