# module-kind: declarative
"""Persistence event constants for the trust_state sub-domain."""

from typing import Final

PERSISTENCE_TRUST_STATE_SAVED: Final[str] = "persistence.trust_state.saved"
PERSISTENCE_TRUST_STATE_SAVE_FAILED: Final[str] = "persistence.trust_state.save_failed"
PERSISTENCE_TRUST_STATE_DELETE_FAILED: Final[str] = (
    "persistence.trust_state.delete_failed"
)
PERSISTENCE_TRUST_STATE_QUERIED: Final[str] = "persistence.trust_state.queried"
PERSISTENCE_TRUST_STATE_QUERY_FAILED: Final[str] = (
    "persistence.trust_state.query_failed"
)
PERSISTENCE_TRUST_STATE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.trust_state.deserialize_failed"
)
