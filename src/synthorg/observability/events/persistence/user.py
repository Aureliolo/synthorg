# module-kind: declarative
"""Persistence event constants for the user sub-domain."""

from typing import Final

PERSISTENCE_USER_SAVED: Final[str] = "persistence.user.saved"
PERSISTENCE_USER_SAVE_FAILED: Final[str] = "persistence.user.save_failed"
PERSISTENCE_USER_FETCHED: Final[str] = "persistence.user.fetched"
PERSISTENCE_USER_FETCH_FAILED: Final[str] = "persistence.user.fetch_failed"
PERSISTENCE_USER_LISTED: Final[str] = "persistence.user.listed"
PERSISTENCE_USER_LIST_FAILED: Final[str] = "persistence.user.list_failed"
PERSISTENCE_USER_COUNTED: Final[str] = "persistence.user.counted"
PERSISTENCE_USER_COUNT_FAILED: Final[str] = "persistence.user.count_failed"
PERSISTENCE_USER_COUNTED_BY_ROLE: Final[str] = "persistence.user.counted_by_role"
PERSISTENCE_USER_COUNT_BY_ROLE_FAILED: Final[str] = (
    "persistence.user.count_by_role_failed"
)
PERSISTENCE_USER_DELETED: Final[str] = "persistence.user.deleted"
PERSISTENCE_USER_DELETE_FAILED: Final[str] = "persistence.user.delete_failed"
