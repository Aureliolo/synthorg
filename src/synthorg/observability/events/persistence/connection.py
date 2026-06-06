# module-kind: declarative
"""Persistence event constants for the connection sub-domain (durable catalog)."""

from typing import Final

PERSISTENCE_CONNECTION_SAVED: Final[str] = "persistence.connection.saved"
PERSISTENCE_CONNECTION_SAVE_FAILED: Final[str] = "persistence.connection.save_failed"
PERSISTENCE_CONNECTION_FETCHED: Final[str] = "persistence.connection.fetched"
PERSISTENCE_CONNECTION_FETCH_FAILED: Final[str] = "persistence.connection.fetch_failed"
PERSISTENCE_CONNECTION_LISTED: Final[str] = "persistence.connection.listed"
PERSISTENCE_CONNECTION_LIST_FAILED: Final[str] = "persistence.connection.list_failed"
PERSISTENCE_CONNECTION_DELETED: Final[str] = "persistence.connection.deleted"
PERSISTENCE_CONNECTION_DELETE_FAILED: Final[str] = (
    "persistence.connection.delete_failed"
)
PERSISTENCE_CONNECTION_DESERIALIZE_FAILED: Final[str] = (
    "persistence.connection.deserialize_failed"
)
