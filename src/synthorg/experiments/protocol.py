"""Repository protocol for the A/B experiment registry."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.core.types import (
    NotBlankStr,
)
from synthorg.experiments.models import (
    ExperimentAssignment,
    ExperimentVariant,
)


@runtime_checkable
class ExperimentRepository(Protocol):
    """Persistence boundary for variant registration and assignment."""

    async def save(self, variant: ExperimentVariant) -> None:
        """Insert or replace ``variant`` keyed on ``(experiment, variant)``."""
        ...

    async def list_for_experiment(
        self,
        experiment: NotBlankStr,
    ) -> tuple[ExperimentVariant, ...]:
        """Return every registered variant for ``experiment``.

        Ordering is by registration timestamp (oldest first) so the
        assignment hash walk is deterministic across processes.
        """
        ...

    async def delete(
        self,
        *,
        experiment: NotBlankStr,
        variant: NotBlankStr,
    ) -> bool:
        """Remove a variant. Returns ``True`` when a row was deleted."""
        ...

    async def record_assignment(
        self,
        assignment: ExperimentAssignment,
    ) -> None:
        """Insert or update the row keyed on ``(experiment, subject_id)``.

        Durable backends may enforce a unique constraint on the
        composite key and raise
        :class:`synthorg.core.domain_errors.ConflictError` when a
        concurrent writer lands the row first. Callers handle the
        conflict by re-reading via :meth:`get_assignment`.
        """
        ...

    async def get_assignment(
        self,
        *,
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
    ) -> ExperimentAssignment | None:
        """Return the previously-recorded assignment, or ``None`` if absent."""
        ...

    async def list_assignments(
        self,
        experiment: NotBlankStr,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ExperimentAssignment, ...], int]:
        """Return ``(page, total)`` for the experiment's assignments.

        Ordering is by ``assigned_at`` descending so the most recent
        assignments appear first; total carries the unbounded count so
        the controller can render pagination metadata.
        """
        ...

    async def assigned_at(self, *, now: datetime) -> datetime:
        """Return the canonical assignment timestamp for repository writes.

        The default implementation echoes ``now``; backends override
        when they need to normalise to their own clock (e.g. for
        replay-safe ordering).
        """
        ...
