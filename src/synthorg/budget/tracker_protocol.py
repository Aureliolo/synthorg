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

from synthorg.budget.category_analytics import OrchestrationRatio
from synthorg.budget.config import BudgetConfig
from synthorg.budget.coordination_config import OrchestrationAlertThresholds
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.spending_summary import SpendingSummary
from synthorg.core.pagination import DEFAULT_LIST_LIMIT
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

    async def get_records(  # noqa: PLR0913 -- orthogonal filters + pagination
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        prompt_class_id: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Return a bounded page of records matching the filters.

        Returns one ``limit``-sized page in insertion order
        (oldest-first), matching the concrete implementation, so a
        cursor walk is repeatable. Callers needing every matching record
        use :meth:`collect_records` (or :func:`collect_all_records`) to read
        one atomic snapshot rather than walking successive pages, whose
        offsets a concurrent prune could shift (a TOCTOU drop hazard).
        """
        ...

    async def collect_records(  # noqa: PLR0913 -- orthogonal filters
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        prompt_class_id: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return every matching record from ONE atomic snapshot.

        Aggregators that need the complete filtered set use this rather than a
        paginated :meth:`get_records` walk: a single snapshot cannot have its
        offsets shifted by a concurrent prune, so no record is dropped mid-drain.
        """
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

    async def get_orchestration_ratio(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        thresholds: OrchestrationAlertThresholds | None = None,
    ) -> OrchestrationRatio:
        """Compute the orchestration overhead ratio for the window."""
        ...

    def track_pending_record(self, task: asyncio.Task[None]) -> None:
        """Register an in-flight record-write task for graceful drain."""
        ...

    async def drain_pending_records(self) -> None:
        """Await every in-flight record-write task registered for drain."""
        ...


async def collect_all_records(  # noqa: PLR0913 -- orthogonal record filters
    tracker: CostTrackerProtocol,
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    provider: NotBlankStr | None = None,
    prompt_class_id: NotBlankStr | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[CostRecord, ...]:
    """Return every matching record from the tracker in one atomic snapshot.

    Delegates to :meth:`CostTrackerProtocol.collect_records`, which filters a
    single snapshot. This replaced an offset-paginated ``get_records`` walk
    whose successive snapshots could be re-pruned between pages, silently
    dropping records (a TOCTOU). The materialised result is identical; only the
    drop hazard is removed.

    Returns:
        Every matching record, oldest-first.
    """
    return await tracker.collect_records(
        agent_id=agent_id,
        task_id=task_id,
        provider=provider,
        prompt_class_id=prompt_class_id,
        start=start,
        end=end,
    )
