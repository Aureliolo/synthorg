"""Research-subsystem persistence protocol.

A single repository backs research mode:

* :class:`ResearchRunRepository` keyed by ``run_id`` -- the durable,
  replayable record of each research run (its brief snapshot, query plan,
  retrieved items, credibility verdicts, and final report).

The run owns an immutable snapshot of its brief, so no separate brief
table is needed; ``brief_id`` / ``project_id`` are denormalised onto the
row for filtering. The protocol composes the generic categories and adds
no bespoke methods (listing runs for a brief is an ordinary filtered
query).
"""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)
from synthorg.research.enums import ResearchRunStatus
from synthorg.research.models import ResearchRun

ResearchRunKey = NotBlankStr
"""Single-string ``run_id`` PK type alias."""


class ResearchRunFilter(BaseModel):
    """Filter spec for :meth:`ResearchRunRepository.query`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_id: NotBlankStr | None = Field(
        default=None,
        description="Restrict to runs of this brief",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Restrict to runs in this project",
    )
    status: ResearchRunStatus | None = Field(
        default=None,
        description="Optional run-status filter",
    )


@runtime_checkable
class ResearchRunRepository(
    IdKeyedRepository[ResearchRun, ResearchRunKey],
    FilteredQueryRepository[ResearchRun, ResearchRunFilter],
    Protocol,
):
    """CRUD + filtered-query interface for :class:`ResearchRun` rows.

    ``save`` is an upsert keyed by ``run_id`` so a run row is updated in
    place as it advances through its lifecycle states.

    Ordering invariant: :meth:`list_items` and :meth:`query` return rows in
    descending ``created_at`` order (most-recent first), with ``run_id`` as
    a stable tie-breaker.
    """

    @override
    async def save(self, entity: ResearchRun, /) -> None:
        """Persist a run row via upsert (PK ``run_id``)."""
        ...

    @override
    async def get(self, entity_id: ResearchRunKey, /) -> ResearchRun | None:
        """Retrieve a run by ``run_id``, or ``None`` when absent."""
        ...

    @override
    async def delete(self, entity_id: ResearchRunKey, /) -> bool:
        """Delete a run by ``run_id``. ``True`` iff a row existed."""
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """List runs across all briefs, most-recent first."""
        ...

    @override
    async def query(
        self,
        filter_spec: ResearchRunFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """Return runs matching the filter, most-recent first."""
        ...

    @override
    async def count(self, filter_spec: ResearchRunFilter) -> int:
        """Count runs matching the filter spec."""
        ...
