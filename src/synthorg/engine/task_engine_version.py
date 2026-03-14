"""Version tracking for TaskEngine optimistic concurrency.

Wraps a plain ``dict[str, int]`` with seed, bump, check, and remove
operations.  Extracted from ``task_engine.py`` to keep the main module
focused on lifecycle and queue management.
"""

from synthorg.engine.errors import TaskVersionConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import TASK_ENGINE_VERSION_CONFLICT

logger = get_logger(__name__)


class VersionTracker:
    """In-memory per-task version counter for optimistic concurrency.

    After a restart the tracker is empty.  The first time an unknown
    task is encountered during a ``check()`` call, it is seeded at
    version 1 — a heuristic baseline, **not** loaded from persistence.
    This makes subsequent optimistic-concurrency checks work within the
    current engine lifetime but cannot detect conflicts that span
    restarts.

    **Limitation:** version tracking is volatile — it resets on process
    restart.  After a restart, the first ``expected_version=1`` check
    for any task will pass even if the task was mutated many times in a
    prior lifetime.  Durable version tracking (persisted alongside the
    task) is a future enhancement.

    This class is designed for single-writer access from the
    ``TaskEngine`` processing loop and is **not** thread-safe.
    """

    def __init__(self) -> None:
        self._versions: dict[str, int] = {}

    def seed(self, task_id: str) -> None:
        """Ensure *task_id* has a baseline version (idempotent)."""
        if task_id not in self._versions:
            self._versions[task_id] = 1

    def set_initial(self, task_id: str, version: int) -> None:
        """Set *task_id* to *version* unconditionally (used on create).

        Raises:
            ValueError: If *version* is less than 1.
        """
        if version < 1:
            msg = f"Version must be >= 1, got {version}"
            raise ValueError(msg)
        self._versions[task_id] = version

    def bump(self, task_id: str) -> int:
        """Increment and return the version counter for *task_id*.

        If *task_id* is not yet tracked, it is seeded at version 1
        first, so the returned value will be 2 (not 1).
        """
        self.seed(task_id)
        version = self._versions[task_id] + 1
        self._versions[task_id] = version
        return version

    def get(self, task_id: str) -> int:
        """Return the current version (0 if not tracked)."""
        return self._versions.get(task_id, 0)

    def remove(self, task_id: str) -> None:
        """Remove version tracking for a deleted task."""
        self._versions.pop(task_id, None)

    def check(
        self,
        task_id: str,
        expected_version: int | None,
    ) -> None:
        """Raise ``TaskVersionConflictError`` if versions disagree.

        Seeds the version at 1 if the task is not yet tracked so that
        optimistic concurrency works within the current engine lifetime.
        """
        if expected_version is None:
            return
        self.seed(task_id)
        current = self._versions[task_id]
        if current != expected_version:
            msg = (
                f"Version conflict for task {task_id!r}: "
                f"expected {expected_version}, current {current}"
            )
            logger.warning(
                TASK_ENGINE_VERSION_CONFLICT,
                task_id=task_id,
                expected_version=expected_version,
                current_version=current,
            )
            raise TaskVersionConflictError(msg)
