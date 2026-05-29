"""CostRecord repository protocol."""

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord


class CostRecordFilterSpec(BaseModel):
    """Filter spec for ``CostRecordRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by agent identifier",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by task identifier",
    )


@runtime_checkable
class CostRecordRepository(
    AppendOnlyRepository["CostRecord", CostRecordFilterSpec],
    Protocol,
):
    """Append-only persistence + query/aggregation for CostRecord.

    Composes :class:`AppendOnlyRepository` (ADR-0001). Bespoke per D7:

    * ``aggregate`` sums total cost with a mixed-currency rejection
      invariant; the generic ``query`` cannot express aggregation.
    """

    @override
    async def append(self, event: CostRecord) -> None:
        """Persist a cost record (append-only).

        Args:
            event: The cost record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: CostRecordFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Query cost records with optional filters and pagination.

        Args:
            filter_spec: Carries optional agent_id and task_id filters.
            limit: Maximum rows to return.
            offset: Rows to skip before applying limit.

        Returns:
            Matching cost records as a tuple, ordered newest-first.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete cost records with timestamp before threshold (retention).

        Args:
            threshold: Records older than this are deleted.

        Returns:
            Number of rows removed.

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
