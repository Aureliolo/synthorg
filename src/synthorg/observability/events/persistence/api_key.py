# module-kind: declarative
"""Persistence event constants for the api_key sub-domain."""

from typing import Final

PERSISTENCE_API_KEY_SAVED: Final[str] = "persistence.api_key.saved"
PERSISTENCE_API_KEY_SAVE_FAILED: Final[str] = "persistence.api_key.save_failed"
PERSISTENCE_API_KEY_FETCHED: Final[str] = "persistence.api_key.fetched"
PERSISTENCE_API_KEY_FETCH_FAILED: Final[str] = "persistence.api_key.fetch_failed"
PERSISTENCE_API_KEY_LISTED: Final[str] = "persistence.api_key.listed"
PERSISTENCE_API_KEY_LIST_FAILED: Final[str] = "persistence.api_key.list_failed"
PERSISTENCE_API_KEY_COUNT_FAILED: Final[str] = "persistence.api_key.count_failed"
PERSISTENCE_API_KEY_DELETED: Final[str] = "persistence.api_key.deleted"
PERSISTENCE_API_KEY_DELETE_FAILED: Final[str] = "persistence.api_key.delete_failed"
