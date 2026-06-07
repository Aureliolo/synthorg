# module-kind: declarative
"""Persistence event constants for the message sub-domain."""

from typing import Final

PERSISTENCE_MESSAGE_SAVED: Final[str] = "persistence.message.saved"
PERSISTENCE_MESSAGE_SAVE_FAILED: Final[str] = "persistence.message.save_failed"
PERSISTENCE_MESSAGE_DUPLICATE: Final[str] = "persistence.message.duplicate"
PERSISTENCE_MESSAGE_HISTORY_FETCHED: Final[str] = "persistence.message.history_fetched"
PERSISTENCE_MESSAGE_HISTORY_FAILED: Final[str] = "persistence.message.history_failed"
PERSISTENCE_MESSAGE_FETCHED: Final[str] = "persistence.message.fetched"
PERSISTENCE_MESSAGE_FETCH_FAILED: Final[str] = "persistence.message.fetch_failed"
PERSISTENCE_MESSAGE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.message.deserialize_failed"
)
PERSISTENCE_MESSAGE_DELETED: Final[str] = "persistence.message.deleted"
PERSISTENCE_MESSAGE_DELETE_FAILED: Final[str] = "persistence.message.delete_failed"
