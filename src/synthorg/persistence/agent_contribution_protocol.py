"""Persistence protocol for the append-only agent-contribution log.

Durably backs :class:`synthorg.hr.performance.tracker.PerformanceTracker`'s
coordination-contribution trail so it survives restarts and is queryable
for retrospective attribution analytics.

``AgentContribution`` has no timestamp of its own, so the repository
stamps a ``recorded_at`` at append time from an injected clock; the
record itself round-trips through a JSON ``payload`` column.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class AgentContributionFilterSpec(BaseModel):
    """Filter spec for :meth:`AgentContributionRepository.query`.

    Attributes:
        agent_id: Restrict to one agent. ``None`` reads every agent.
        subtask_id: Restrict to one subtask. ``None`` reads all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent whose contributions to read; None reads all",
    )
    subtask_id: NotBlankStr | None = Field(
        default=None,
        description="Subtask whose contributions to read; None reads all",
    )


@runtime_checkable
class AgentContributionRepository(
    AppendOnlyRepository[AgentContribution, AgentContributionFilterSpec],
    Protocol,
):
    """Append-only per-agent coordination-contribution log, newest-first.

    :class:`PerformanceTracker` appends one record per attributed
    contribution; reads are for retrospective analytics only.
    """

    @override
    async def query(
        self,
        filter_spec: AgentContributionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AgentContribution, ...]:
        """Return contributions matching the filter, newest-first.

        Args:
            filter_spec: The agent/subtask predicates.
            limit: Maximum records to return.
            offset: Rows to skip before returning ``limit`` rows.

        Returns:
            Matching contributions, newest-first by insertion order.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete contributions recorded before ``threshold``.

        Args:
            threshold: UTC cutoff on the append-time ``recorded_at``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...
