# module-kind: declarative
"""Persistence event constants for the decision_record sub-domain."""

from typing import Final

PERSISTENCE_DECISION_RECORD_SAVED: Final[str] = "persistence.decision_record.saved"
PERSISTENCE_DECISION_RECORD_SAVE_FAILED: Final[str] = (
    "persistence.decision_record.save_failed"
)
PERSISTENCE_DECISION_RECORD_QUERIED: Final[str] = "persistence.decision_record.queried"
PERSISTENCE_DECISION_RECORD_QUERY_FAILED: Final[str] = (
    "persistence.decision_record.query_failed"
)
PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED: Final[str] = (
    "persistence.decision_record.deserialize_failed"
)
