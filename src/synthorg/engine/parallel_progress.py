"""Mutable progress state for parallel agent execution.

Internal counter bag scoped to a single ``execute_group()`` run; it
produces immutable :class:`ParallelProgress` snapshots for the
progress callback.
"""

import dataclasses

from synthorg.engine.parallel_models import ParallelProgress


@dataclasses.dataclass
class _ProgressState:
    """Mutable progress tracking -- internal to ``execute_group()`` scope."""

    group_id: str
    total: int
    completed: int = 0
    in_progress: int = 0
    succeeded: int = 0
    failed: int = 0

    def snapshot(self) -> ParallelProgress:
        """Create a frozen progress snapshot.

        Returns:
            A :class:`ParallelProgress` immutable snapshot of the
            current counters.
        """
        return ParallelProgress(
            group_id=self.group_id,
            total=self.total,
            completed=self.completed,
            in_progress=self.in_progress,
            succeeded=self.succeeded,
            failed=self.failed,
        )
