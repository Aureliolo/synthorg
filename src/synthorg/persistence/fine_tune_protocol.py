"""Repository protocols for fine-tune runs and checkpoints.

Concrete implementations live under ``persistence/sqlite`` and
``persistence/postgres``.  Services (e.g. ``MemoryService``) depend on
these Protocols instead of the SQLite classes so the persistence
backend can be swapped without touching service code.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class FineTuneRunRepository(
    IdKeyedRepository["FineTuneRun", NotBlankStr],
    Protocol,
):
    """Persistence interface for fine-tuning pipeline runs.

    Composes :class:`IdKeyedRepository` (ADR-0001). Bespoke per D7:
    :meth:`get_active_run` filters runs in active pipeline stages
    (domain invariant: callers bypass generic list_items to efficiently
    find the in-progress run); :meth:`update_run` is an optimised
    bulk-field update avoiding full run serialization; :meth:`mark_interrupted`
    atomically marks all active runs as FAILED (startup recovery).
    """

    @override
    async def save(self, entity: FineTuneRun) -> None:
        """Upsert a run by ``id`` (idempotent semantics).

        Args:
            entity: The run to persist.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> FineTuneRun | None:
        """Retrieve a run by ID.

        Args:
            entity_id: The run identifier.

        Returns:
            The run, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a run by ID.

        Args:
            entity_id: The run identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FineTuneRun, ...]:
        """List runs in ``id`` order (paginated).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head.

        Returns:
            Runs in ascending ``id`` order.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def list_items_page(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """List runs in ``id`` order along with total count (paginated).

        Bespoke D7: the generic ``list_items`` does not return a count;
        callers (REST controllers, dashboard) need the total for
        pagination metadata.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head.

        Returns:
            Tuple of ``(runs, total_count)`` where runs are in
            ascending ``id`` order.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get_active_run(self) -> FineTuneRun | None:
        """Return the currently-active run (in an active pipeline stage).

        Bespoke D7: efficiency and domain invariant. Callers must not
        bypass this to find active runs; the implementation may use an
        index on ``stage`` and ``started_at``.

        Returns:
            The active run, or ``None`` if none exists.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def update_run(self, run: FineTuneRun) -> None:
        """Update all mutable fields for a run.

        Bespoke D7: optimisation to avoid serializing the entire
        run when only a subset of columns change (stage, progress,
        error, timestamps, stages_completed).

        Args:
            run: The run with updated fields.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def mark_interrupted(self) -> int:
        """Mark all active runs as ``FAILED`` on startup recovery.

        Bespoke D7: atomic bulk transition (CAS-like) that only
        ``startup`` invokes; not expressible via generic methods.

        Returns:
            Number of runs transitioned to ``FAILED``.

        Raises:
            QueryError: If the operation fails.
        """
        ...


@runtime_checkable
class FineTuneCheckpointRepository(
    IdKeyedRepository["CheckpointRecord", NotBlankStr],
    Protocol,
):
    """Persistence interface for fine-tuning checkpoint records.

    Composes :class:`IdKeyedRepository` (ADR-0001). Bespoke per D7:
    :meth:`list_items_page` returns a count (needed for pagination);
    :meth:`set_active`, :meth:`deactivate_all`, :meth:`get_active_checkpoint`
    manage the ``is_active`` singleton invariant (only one active
    checkpoint at a time, domain enforcement).
    """

    @override
    async def save(self, entity: CheckpointRecord) -> None:
        """Upsert a checkpoint by ``id`` (idempotent semantics).

        Args:
            entity: The checkpoint to persist.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> CheckpointRecord | None:
        """Retrieve a checkpoint by ID.

        Args:
            entity_id: The checkpoint identifier.

        Returns:
            The checkpoint, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a checkpoint by ID.

        Raises when deleting the active checkpoint (domain invariant).

        Args:
            entity_id: The checkpoint identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            QueryError: If attempting to delete the active checkpoint.
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CheckpointRecord, ...]:
        """List checkpoints in ``id`` order (paginated).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head.

        Returns:
            Checkpoints in ascending ``id`` order.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def list_items_page(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        """List checkpoints in creation order descending with total count.

        Bespoke D7: the generic ``list_items`` does not return a count;
        callers (REST controllers, dashboard) need the total for
        pagination metadata. Returns newest-first ordering (creation
        descending) instead of ``id`` order for better UX.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head.

        Returns:
            Tuple of ``(checkpoints, total_count)`` where checkpoints
            are ordered by ``created_at`` descending (newest first).

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def set_active(self, checkpoint_id: NotBlankStr) -> None:
        """Deactivate all checkpoints and atomically activate the given one.

        Bespoke D7: domain invariant enforcement. Raises when
        checkpoint does not exist.

        Args:
            checkpoint_id: The checkpoint to activate.

        Raises:
            QueryError: If the checkpoint does not exist or DB fails.
        """
        ...

    async def deactivate_all(self) -> None:
        """Deactivate every checkpoint (rollback-style).

        Bespoke D7: used during rollback and deploy stages.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get_active_checkpoint(
        self,
    ) -> CheckpointRecord | None:
        """Return the currently-active checkpoint, if any.

        Bespoke D7: efficiency. The active checkpoint is the singleton
        invariant that governs embedder model selection; this method
        is on the hot path (every embedding request checks it).

        Returns:
            The active checkpoint, or ``None`` if none exists.

        Raises:
            QueryError: If the operation fails.
        """
        ...
