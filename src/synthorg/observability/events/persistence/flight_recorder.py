# module-kind: declarative
"""Persistence event constants for the flight_recorder sub-domain."""

from typing import Final

PERSISTENCE_FLIGHT_RECORDER_SAVED: Final[str] = "persistence.flight_recorder.saved"
PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED: Final[str] = (
    "persistence.flight_recorder.save_failed"
)
PERSISTENCE_FLIGHT_RECORDER_QUERIED: Final[str] = "persistence.flight_recorder.queried"
PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED: Final[str] = (
    "persistence.flight_recorder.query_failed"
)
PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED: Final[str] = (
    "persistence.flight_recorder.delete_failed"
)
PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED: Final[str] = (
    "persistence.flight_recorder.deserialize_failed"
)
