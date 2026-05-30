"""Long-horizon project-brain engine event constants.

Structured event names emitted by ``synthorg.project_brain`` services. Loggers
from this module always pass these as the first positional ``event`` argument
and add typed kwargs alongside (per the project's structured-logging
convention).
"""

from typing import Final

BRAIN_ENTRY_APPENDED: Final[str] = "project_brain.entry.appended"
BRAIN_ENTRY_APPEND_FAILED: Final[str] = "project_brain.entry.append_failed"
BRAIN_ENTRY_INDEXED: Final[str] = "project_brain.entry.indexed"
BRAIN_ENTRY_INDEX_FAILED: Final[str] = "project_brain.entry.index_failed"
BRAIN_ENTRY_RETRIEVED: Final[str] = "project_brain.entry.retrieved"
BRAIN_ENTRY_COMMIT_PUSHED: Final[str] = "project_brain.entry.commit_pushed"
BRAIN_ENTRY_NOT_FOUND: Final[str] = "project_brain.entry.not_found"
BRAIN_ENTRY_VALIDATION_FAILED: Final[str] = "project_brain.entry.validation_failed"
BRAIN_SEARCH_START: Final[str] = "project_brain.search.start"
BRAIN_SEARCH_COMPLETE: Final[str] = "project_brain.search.complete"
BRAIN_SEARCH_FAILED: Final[str] = "project_brain.search.failed"
BRAIN_HISTORY_READ: Final[str] = "project_brain.history.read"
BRAIN_FACADE_FANOUT: Final[str] = "project_brain.facade.fanout"
BRAIN_FACADE_FANOUT_FAILED: Final[str] = "project_brain.facade.fanout_failed"
