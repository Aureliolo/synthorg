# module-kind: declarative
"""Persistence event constants for the principle_override sub-domain."""

from typing import Final

PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.principle_override.save_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED: Final[str] = (
    "persistence.principle_override.get_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.principle_override.delete_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED: Final[str] = (
    "persistence.principle_override.list_failed"
)
