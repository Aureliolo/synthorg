"""CostRecord repository protocol."""

from typing import Protocol, runtime_checkable

from synthorg.budget.cost_record import CostRecord  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class CostRecordRepository(Protocol):
    """Append-only persistence + query/aggregation for CostRecord."""

    async def save(self, record: CostRecord) -> None:
        """Persist a cost record (append-only).

        Args:
            record: The cost record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Query cost records with optional filters and pagination.

        Args:
            agent_id: Filter by agent identifier.
            task_id: Filter by task identifier.
            limit: Maximum rows to return; ``None`` (default) preserves
                fetch-all semantics.
            offset: Rows to skip before applying *limit*; ignored when
                *limit* is ``None``.

        Returns:
            Matching cost records as a tuple.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def aggregate(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> float:
        """Sum total cost, optionally filtered by agent and/or task.

        Args:
            agent_id: Filter by agent identifier.
            task_id: Filter by task identifier.

        Returns:
            Total cost in the configured currency.

        Raises:
            MixedCurrencyAggregationError: If the matched cost records
                span more than one currency.  Aggregation is rejected
                rather than silently summing across currencies; the
                controller maps this to HTTP 409.  Filter by
                ``agent_id``/``task_id`` (or by date window in caller
                code) to scope the aggregation to a single currency.
            PersistenceError: If the operation fails.
        """
        ...
