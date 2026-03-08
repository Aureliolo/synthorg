"""Parallel execution event constants."""

from typing import Final

PARALLEL_GROUP_START: Final[str] = "execution.parallel.group_start"
PARALLEL_GROUP_COMPLETE: Final[str] = "execution.parallel.group_complete"
PARALLEL_AGENT_START: Final[str] = "execution.parallel.agent_start"
PARALLEL_AGENT_COMPLETE: Final[str] = "execution.parallel.agent_complete"
PARALLEL_AGENT_ERROR: Final[str] = "execution.parallel.agent_error"
PARALLEL_LOCK_ACQUIRED: Final[str] = "execution.parallel.lock_acquired"
PARALLEL_LOCK_RELEASED: Final[str] = "execution.parallel.lock_released"
PARALLEL_LOCK_CONFLICT: Final[str] = "execution.parallel.lock_conflict"
PARALLEL_PROGRESS_UPDATE: Final[str] = "execution.parallel.progress_update"
PARALLEL_VALIDATION_ERROR: Final[str] = "execution.parallel.validation_error"
PARALLEL_AGENT_CANCELLED: Final[str] = "execution.parallel.agent_cancelled"
PARALLEL_LOCK_RELEASE_ERROR: Final[str] = "execution.parallel.lock_release_error"
PARALLEL_GROUP_SUPPRESSED: Final[str] = "execution.parallel.group_suppressed"
