# module-kind: declarative
"""EvolutionOutcomeRecord repository protocol.

Append-only durable log of the terminal outcome of every evolution
proposal the engine evolution loop processes. The in-memory ring-buffer
store stays the hot read; this durable repo is the restart-surviving log
that backs the ``/meta/evolution/*`` read endpoints and rehydrates the
ring buffer on boot.
"""

from datetime import datetime
from typing import Protocol, Self, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class EvolutionOutcomeFilterSpec(BaseModel):
    """Filter spec for ``EvolutionOutcomeRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by the agent the proposal targeted.",
    )
    axis: NotBlankStr | None = Field(
        default=None,
        description="Filter by adaptation axis.",
    )
    applied: bool | None = Field(
        default=None,
        description="Filter by terminal applied/not-applied outcome.",
    )
    since: AwareDatetime | None = Field(
        default=None,
        description="Only outcomes recorded at or after this instant.",
    )
    until: AwareDatetime | None = Field(
        default=None,
        description="Only outcomes recorded strictly before this instant.",
    )

    @model_validator(mode="after")
    def _validate_window_order(self) -> Self:
        """Reject an inverted ``[since, until)`` window.

        An inverted window would silently return zero rows from the
        backend rather than surfacing the misconfiguration.

        Returns:
            The validated instance.

        Raises:
            ValueError: When both bounds are set and ``since >= until``.
        """
        since = self.since
        until = self.until
        if since is not None and until is not None and since >= until:
            msg = (
                f"since ({since.isoformat()}) must be earlier than "
                f"until ({until.isoformat()})"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class EvolutionOutcomeRepository(
    AppendOnlyRepository["EvolutionOutcomeRecord", EvolutionOutcomeFilterSpec],
    Protocol,
):
    """Append-only persistence + windowed aggregation for outcome records.

    Composes :class:`AppendOnlyRepository` (ADR-0001). Bespoke per D7:

    * ``axis_counts`` aggregates outcomes per axis within a window; the
      generic ``query`` cannot express a GROUP BY and the axes-stats
      endpoint must not page the whole table to count.
    """

    @override
    async def append(self, event: EvolutionOutcomeRecord, /) -> None:
        """Persist an evolution outcome (append-only).

        Args:
            event: The outcome record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: EvolutionOutcomeFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """Query outcomes with optional filters and pagination.

        Args:
            filter_spec: Optional agent / axis / applied / window filters.
            limit: Maximum rows to return.
            offset: Rows to skip before applying limit.

        Returns:
            Matching outcomes as a tuple, ordered newest-first by
            ``recorded_at``.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete outcomes recorded before threshold (retention).

        Args:
            threshold: Outcomes older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def axis_counts(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[tuple[NotBlankStr, int], ...]:
        """Count outcomes per axis recorded within ``[since, until)``.

        Args:
            since: Window start (inclusive); must be timezone-aware.
            until: Window end (exclusive); must be timezone-aware.

        Returns:
            ``(axis, count)`` pairs ordered by descending count then
            axis name for determinism.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


__all__ = ["EvolutionOutcomeFilterSpec", "EvolutionOutcomeRepository"]
