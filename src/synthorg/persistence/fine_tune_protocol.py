"""Repository protocols for fine-tune runs and checkpoints.

Concrete implementations live under ``persistence/sqlite`` and
``persistence/postgres``.  Services (e.g. ``MemoryService``) depend on
these Protocols instead of the SQLite classes so the persistence
backend can be swapped without touching service code.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,  # noqa: TC001
    FineTuneRun,  # noqa: TC001
)

_DEFAULT_LIST_LIMIT_50: Final[int] = 50


@runtime_checkable
class FineTuneRunRepository(Protocol):
    """Persistence interface for fine-tuning pipeline runs."""

    async def save_run(self, run: FineTuneRun) -> None:
        """Persist a run (upsert semantics)."""
        ...

    async def get_run(self, run_id: str) -> FineTuneRun | None:
        """Retrieve a run by ID, or ``None`` if it does not exist."""
        ...

    async def get_active_run(self) -> FineTuneRun | None:
        """Return the currently-active run, if any."""
        ...

    async def list_runs(
        self,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_50,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """List runs ordered by start time descending.

        Returns:
            Tuple of ``(runs, total_count)``.
        """
        ...

    async def update_run(self, run: FineTuneRun) -> None:
        """Update all mutable fields for a run."""
        ...

    async def mark_interrupted(self) -> int:
        """Mark all active runs as ``FAILED`` on startup recovery.

        Returns:
            Number of runs transitioned to ``FAILED``.
        """
        ...


@runtime_checkable
class FineTuneCheckpointRepository(Protocol):
    """Persistence interface for fine-tuning checkpoint records."""

    async def save_checkpoint(
        self,
        checkpoint: CheckpointRecord,
    ) -> None:
        """Persist a checkpoint (upsert semantics)."""
        ...

    async def get_checkpoint(
        self,
        checkpoint_id: str,
    ) -> CheckpointRecord | None:
        """Retrieve a checkpoint by ID, or ``None`` if it does not exist."""
        ...

    async def list_checkpoints(
        self,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_50,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        """List checkpoints ordered by creation time descending.

        Returns:
            Tuple of ``(checkpoints, total_count)``.
        """
        ...

    async def set_active(self, checkpoint_id: str) -> None:
        """Deactivate all checkpoints and activate the given one atomically."""
        ...

    async def deactivate_all(self) -> None:
        """Deactivate every checkpoint (rollback-style)."""
        ...

    async def delete_checkpoint(
        self,
        checkpoint_id: str,
    ) -> None:
        """Delete a checkpoint; raises when deleting the active checkpoint."""
        ...

    async def get_active_checkpoint(
        self,
    ) -> CheckpointRecord | None:
        """Return the currently-active checkpoint, if any."""
        ...
