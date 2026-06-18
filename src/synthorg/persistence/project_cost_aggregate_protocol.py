"""Repository protocol for durable per-project cost aggregates.

Lives under ``persistence/`` (not ``budget/``) because every durable
feature that ``PersistenceBackend`` exposes must define its repository
Protocol in ``persistence/<domain>_protocol.py`` per project convention.

The value object (:class:`ProjectCostAggregate`) stays in
``budget/project_cost_aggregate`` because budget code consumes it.

The protocol enforces the currency-aware invariant described in
CLAUDE.md: cost-bearing aggregations must reject mixed-currency
increments via :class:`MixedCurrencyAggregationError`.  ``increment``
takes a required ``currency`` keyword argument; concrete backends
validate that the project's existing currency (if any) matches the
incoming currency and raise on mismatch.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.budget.currency import CurrencyCode
from synthorg.budget.project_cost_aggregate import (
    ProjectCostAggregate,
)
from synthorg.core.types import NotBlankStr


@runtime_checkable
class ProjectCostAggregateRepository(Protocol):
    """Repository for durable per-project cost aggregates.

    Implementations must provide atomic increment semantics so
    concurrent cost recordings do not lose updates.
    """

    async def get(self, project_id: NotBlankStr, /) -> ProjectCostAggregate | None:
        """Retrieve the aggregate for a project.

        Returns:
            The aggregate, or ``None`` if no costs have been recorded.
        """
        ...

    async def increment(
        self,
        project_id: NotBlankStr,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: CurrencyCode,
    ) -> ProjectCostAggregate:
        """Atomically increment the project's cost aggregate.

        Creates a new aggregate row on the first call for a project.
        Subsequent calls increment the existing totals only when the
        incoming ``currency`` matches the aggregate's currency; a
        mismatch raises :class:`MixedCurrencyAggregationError`.

        Args:
            project_id: Project to increment.
            cost: Cost amount denominated in ``currency``.
            input_tokens: Input token count to add.
            output_tokens: Output token count to add.
            currency: ISO 4217 currency for ``cost``.

        Returns:
            The updated aggregate after the increment.

        Raises:
            MixedCurrencyAggregationError: If the project already has
                an aggregate row in a different currency.
        """
        ...

    async def increment_if_unseen(  # noqa: PLR0913 -- aggregate + dedup params
        self,
        project_id: NotBlankStr,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: CurrencyCode,
        claim_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> tuple[ProjectCostAggregate | None, bool]:
        """Atomically dedup-and-increment in one transaction.

        Inserts the ``claim_id`` dedup row and, only when it is new,
        increments the project aggregate -- both in a single
        transaction so a crash between the two writes cannot leave the
        aggregate incremented without its dedup row (which would
        re-bill the claim on redelivery). When the claim was already
        recorded the increment is skipped.

        Args:
            project_id: Project to increment.
            cost: Cost amount denominated in ``currency``.
            input_tokens: Input token count to add.
            output_tokens: Output token count to add.
            currency: ISO 4217 currency for ``cost``.
            claim_id: Idempotency key for the cost record.
            now: Timestamp the dedup row is stamped with.
            ttl_seconds: Retention window for the dedup row.

        Returns:
            ``(aggregate, True)`` when the claim was new and the
            increment was applied; ``(None, False)`` when the claim was
            already recorded and the increment was skipped.

        Raises:
            MixedCurrencyAggregationError: If the project already has
                an aggregate row in a different currency.
        """
        ...
