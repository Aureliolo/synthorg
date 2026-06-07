# module-kind: declarative
"""Persistence event constants for the setting sub-domain."""

from typing import Final

PERSISTENCE_SETTING_FETCHED: Final[str] = "persistence.setting.fetched"
PERSISTENCE_SETTING_FETCH_FAILED: Final[str] = "persistence.setting.fetch_failed"
PERSISTENCE_SETTING_SAVED: Final[str] = "persistence.setting.saved"
PERSISTENCE_SETTING_SAVE_FAILED: Final[str] = "persistence.setting.save_failed"
