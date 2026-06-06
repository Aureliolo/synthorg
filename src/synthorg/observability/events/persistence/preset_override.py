# module-kind: declarative
"""Persistence event constants for the preset_override sub-domain."""

from typing import Final

PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.preset_override.save_failed"
)
PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED: Final[str] = (
    "persistence.preset_override.query_failed"
)
PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.preset_override.delete_failed"
)
