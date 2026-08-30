# module-kind: declarative
"""Persistence event constants for the background_job sub-domain (sandboxes)."""

from typing import Final

PERSISTENCE_BACKGROUND_JOB_SAVE_FAILED: Final[str] = (
    "persistence.background_job.save_failed"
)
PERSISTENCE_BACKGROUND_JOB_LOADED: Final[str] = "persistence.background_job.loaded"
PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED: Final[str] = (
    "persistence.background_job.load_failed"
)
PERSISTENCE_BACKGROUND_JOB_DELETE_FAILED: Final[str] = (
    "persistence.background_job.delete_failed"
)
