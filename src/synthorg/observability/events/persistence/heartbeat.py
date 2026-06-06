# module-kind: declarative
"""Persistence event constants for the heartbeat sub-domain."""

from typing import Final

PERSISTENCE_HEARTBEAT_SAVED: Final[str] = "persistence.heartbeat.saved"
PERSISTENCE_HEARTBEAT_SAVE_FAILED: Final[str] = "persistence.heartbeat.save_failed"
PERSISTENCE_HEARTBEAT_QUERIED: Final[str] = "persistence.heartbeat.queried"
PERSISTENCE_HEARTBEAT_QUERY_FAILED: Final[str] = "persistence.heartbeat.query_failed"
PERSISTENCE_HEARTBEAT_NOT_FOUND: Final[str] = "persistence.heartbeat.not_found"
PERSISTENCE_HEARTBEAT_DELETED: Final[str] = "persistence.heartbeat.deleted"
PERSISTENCE_HEARTBEAT_DELETE_FAILED: Final[str] = "persistence.heartbeat.delete_failed"
PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.heartbeat.deserialize_failed"
)
