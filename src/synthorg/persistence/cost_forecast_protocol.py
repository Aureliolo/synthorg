"""Repository protocol for pre-flight cost forecasts.

A :class:`Forecast` row is the durable record of a pre-flight cost
estimate that gates a brief's dispatch into the work pipeline. The
repository composes :class:`StatefulRepository` (atomic decision
transitions: ``pending -> approved | rejected | superseded``) and
:class:`FilteredQueryRepository` (lookup by ``brief_hash`` and / or
``decision``, which the work-entry adapter needs to find an existing
pending row before issuing a fresh forecast).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, override, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)


class CostForecastFilterSpec(BaseModel):
    """Filter spec for ``CostForecastRepository.query``.

    All fields optional; an empty spec matches every forecast. The
    work-entry adapter typically queries by ``brief_hash`` plus
    ``decision=PENDING`` to find a re-usable pending row before
    issuing a fresh forecast for the same brief.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_hash: NotBlankStr | None = Field(default=None)
    decision: ForecastDecision | None = Field(default=None)


@runtime_checkable
class CostForecastRepository(
    StatefulRepository[Forecast, UUID, ForecastDecision],
    FilteredQueryRepository[Forecast, CostForecastFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for cost forecasts.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Two bespoke methods, :meth:`claim_if_unclaimed` and
    :meth:`raise_ceiling_if_halted`, are sanctioned under ADR-0001 D7
    (domain invariants callers must not bypass): claiming a standalone
    estimate and resuming a halted run are each a read-modify-write that
    must happen exactly once, which the generic ``save`` cannot express.

    Implementations enforce the same-currency invariant on
    :meth:`save`: the incoming :attr:`Forecast.currency` MUST equal
    the currently-configured ``budget.currency`` setting. Mismatched
    writes raise :class:`MixedCurrencyAggregationError` at the
    repository boundary so a silent re-stamp cannot produce a
    meaningless aggregate later.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: Forecast, /) -> None:
        """Upsert a cost forecast row keyed by ``forecast_id``.

        Raises:
            ConstraintViolationError: On constraint violations
                (including the unique-pending invariant on
                ``brief_hash`` and the same-currency check).
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: UUID, /) -> Forecast | None:
        """Retrieve a forecast by ``forecast_id``, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: UUID, /) -> bool:
        """Delete a forecast by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        """List forecasts, newest-first (``created_at DESC, forecast_id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        /,
        entity_id: UUID,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the decision state.

        ``**updates`` MAY include ``decided_by`` (NotBlankStr),
        ``decided_at`` (UTC ``datetime``), and ``ceiling_amount``
        (float). Implementations validate types at the boundary and
        reject unknown keys with :class:`QueryError`. ``decided_at``
        defaults to ``utcnow()`` when omitted; ``decided_by`` is
        required for ``approved`` / ``rejected`` and forbidden for
        ``superseded`` (the system itself supersedes; no operator
        identity attaches).

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors or an invalid update key.
        """
        ...

    async def claim_if_unclaimed(
        self,
        entity_id: UUID,
        *,
        gated_work_item: Mapping[str, JsonValue],
        brief_hash: NotBlankStr,
        updated_at: datetime,
    ) -> bool:
        """Attach the work item and re-key the digest, only if still free.

        Optimistic-concurrency conditional write (ADR-0001 D7): an
        estimate raised on its own belongs to the submission that reaches
        it first, and what that submission may then do with it is spend
        the operator's approved ceiling. Read-then-write would let two
        concurrent submissions each see a free row and each believe it
        won, so the condition lives in the statement and the loser is
        told it lost rather than silently sharing one approval.

        Returns:
            ``True`` when the row was free and is now this submission's;
            ``False`` when another submission already claimed it or the
            row is missing.

        Raises:
            QueryError: On database errors.
        """
        ...

    async def raise_ceiling_if_halted(
        self,
        entity_id: UUID,
        *,
        new_ceiling: float,
        updated_at: datetime,
    ) -> bool:
        """Raise the ceiling and clear the halt, only if still halted.

        Optimistic-concurrency conditional write (ADR-0001 D7): the
        update applies only while the row is halted, so a concurrent
        ceiling-raise that already resumed the run leaves the row
        unmatched and the loser is told it lost rather than silently
        succeeding on a no-op.

        Returns:
            ``True`` when the halted row was updated; ``False`` when the
            row was not halted (already resumed) or is missing.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        """Return forecasts matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, forecast_id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        """Count forecasts matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
