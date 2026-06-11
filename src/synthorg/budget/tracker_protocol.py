"""Structural contract for the cost tracker.

Budget consumers (the enforcer, optimiser, report generators, cost-recording
sinks) depend on the record/aggregate surface, not on the concrete
:class:`~synthorg.budget.tracker.CostTracker`. Annotating against this
``@runtime_checkable`` Protocol lets them hold the tracker structurally, so the
real class and the autospec test doubles satisfy it.

The surface is the runtime-resolvable consumed contract; ``get_provider_usage``
is excluded because its ``ProviderUsageSummary`` return type is a NamedTuple
welded into the concrete ``tracker`` module, and its sole caller holds the
concrete tracker.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.spending_summary import SpendingSummary
from synthorg.core.types import NotBlankStr


@runtime_checkable
class CostTrackerProtocol(Protocol):
    """Record cost events and read back aggregates and snapshots."""

    @property
    def budget_config(self) -> BudgetConfig | None:
        """The configured budget, when one is wired."""
        ...

    async def record(self, cost_record: CostRecord) -> None:
        """Persist a single cost record, deduping by ``claim_id``."""
        ...

    async def get_total_cost(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum cost across all records, optionally time-filtered."""
        ...

    async def get_agent_cost(
        self,
        agent_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum cost for a single agent, optionally time-filtered."""
        ...

    async def get_project_cost(
        self,
        project_id: NotBlankStr,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum cost for a single project, optionally time-filtered."""
        ...

    async def get_records(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return an immutable snapshot of records matching the filters."""
        ...

    async def build_summary(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> SpendingSummary:
        """Build a spending summary for the period from a fresh snapshot."""
        ...

    def build_summary_from_records(
        self,
        records: Sequence[CostRecord],
        *,
        start: datetime,
        end: datetime,
    ) -> SpendingSummary:
        """Build a spending summary from a pre-fetched records snapshot."""
        ...

    def track_pending_record(self, task: asyncio.Task[None]) -> None:
        """Register an in-flight record-write task for graceful drain."""
        ...
