"""Checkpoint and heartbeat repository protocols."""

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

if TYPE_CHECKING:
    from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat

__all__ = [
    "CheckpointFilterSpec",
    "CheckpointRepository",
    "HeartbeatRepository",
]


class CheckpointFilterSpec(BaseModel):
    """Filter spec for ``CheckpointRepository.query``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single execution",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single task",
    )


@runtime_checkable
class CheckpointRepository(
    AppendOnlyRepository["Checkpoint", CheckpointFilterSpec],
    Protocol,
):
    """Append-only persistence interface for checkpoint rows.

    Composes :class:`AppendOnlyRepository`.

    * ``get_latest`` returns the single newest row by ``turn_number``
      under a filter; the generic ``query`` cannot express the
      ``LIMIT 1 ORDER BY turn_number DESC`` shape efficiently.
    * ``delete_by_execution`` is a batch delete keyed on
      ``execution_id``; the generic ``purge_before(threshold)``
      removes only by timestamp, not by execution scope.
    """

    @override
    async def append(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint row (append-only).

        Args:
            checkpoint: The checkpoint to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: CheckpointFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Checkpoint, ...]:
        """Return checkpoints matching the filter, newest first."""
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete checkpoints with ``saved_at < threshold``.

        ``threshold`` must be timezone-aware UTC; naive datetimes are
        rejected at the boundary so purge cut-offs do not depend on the
        caller's local-time assumption.
        """
        ...

    async def get_latest(
        self,
        *,
        execution_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> Checkpoint | None:
        """Retrieve the latest checkpoint by turn_number.

        At least one filter (``execution_id`` or ``task_id``) is required.

        Args:
            execution_id: Filter by execution identifier.
            task_id: Filter by task identifier.

        Returns:
            The checkpoint with the highest turn_number, or ``None``.

        Raises:
            PersistenceError: If the operation fails.
            ValueError: If neither filter is provided.
        """
        ...

    async def delete_by_execution(self, execution_id: NotBlankStr) -> int:
        """Delete all checkpoints for an execution.

        Args:
            execution_id: The execution identifier.

        Returns:
            Number of checkpoints deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


@runtime_checkable
class HeartbeatRepository(Protocol):
    """CRUD interface for heartbeat persistence.

    Heartbeats are a "singleton per execution" (one row per
    ``execution_id``) but the dominant access pattern is
    :meth:`get_stale` (range query over ``last_heartbeat_at``), which
    is not expressible in the generic categories. The save/get/delete
    surface looks superficially like :class:`IdKeyedRepository`, but
    composing that protocol would require ``list_items`` pagination
    that no caller needs while still leaving ``get_stale`` outside the
    generic surface. A fully bespoke protocol is simpler than splitting
    awareness across two surfaces.
    """

    async def save(self, heartbeat: Heartbeat) -> None:
        """Persist a heartbeat (upsert by execution_id).

        Args:
            heartbeat: The heartbeat to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, execution_id: NotBlankStr) -> Heartbeat | None:
        """Retrieve a heartbeat by execution ID.

        Args:
            execution_id: The execution identifier.

        Returns:
            The heartbeat, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_stale(
        self,
        threshold: datetime,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Heartbeat, ...]:
        """Retrieve a bounded page of heartbeats older than the threshold.

        Args:
            threshold: Heartbeats with ``last_heartbeat_at`` before
                this timestamp are considered stale.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of stale heartbeats ordered by ``last_heartbeat_at``
            then ``execution_id`` (stable secondary key for
            deterministic paging). Callers needing every stale
            heartbeat drain via
            :func:`synthorg.persistence._shared.collect_all`.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, execution_id: NotBlankStr) -> bool:
        """Delete a heartbeat by execution ID.

        Args:
            execution_id: The execution identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
