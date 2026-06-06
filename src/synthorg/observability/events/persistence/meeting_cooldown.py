# module-kind: declarative
"""Persistence event constants for the meeting_cooldown sub-domain."""

from typing import Final

PERSISTENCE_MEETING_COOLDOWN_UPSERTED: Final[str] = (
    "persistence.meeting_cooldown.upserted"
)
PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED: Final[str] = (
    "persistence.meeting_cooldown.upsert_failed"
)
PERSISTENCE_MEETING_COOLDOWN_LOADED: Final[str] = "persistence.meeting_cooldown.loaded"
PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED: Final[str] = (
    "persistence.meeting_cooldown.load_failed"
)
PERSISTENCE_MEETING_COOLDOWN_DELETED: Final[str] = (
    "persistence.meeting_cooldown.deleted"
)
PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED: Final[str] = (
    "persistence.meeting_cooldown.delete_failed"
)
