"""Circuit breaker state persistence protocol and model."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


class CircuitBreakerStateRecord(BaseModel):
    """Persistent state for a single agent-pair circuit breaker.

    Attributes:
        pair_key_a: First agent ID (lexicographically smaller).
        pair_key_b: Second agent ID (lexicographically larger).
        bounce_count: Delegations since last reset.
        trip_count: Number of times the circuit has tripped.
        opened_at: Monotonic timestamp when opened, or ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pair_key_a: NotBlankStr = Field(description="First agent ID (sorted)")
    pair_key_b: NotBlankStr = Field(description="Second agent ID (sorted)")
    bounce_count: int = Field(ge=0, description="Bounces since last reset")
    trip_count: int = Field(ge=0, description="Lifetime trip count")
    opened_at: float | None = Field(
        default=None,
        description="Monotonic timestamp when circuit opened",
    )


CircuitBreakerPairKey = tuple[NotBlankStr, NotBlankStr]
"""Composite primary key: ``(pair_key_a, pair_key_b)``."""


@runtime_checkable
class CircuitBreakerStateRepository(
    IdKeyedRepository[CircuitBreakerStateRecord, CircuitBreakerPairKey],
    Protocol,
):
    """CRUD interface for circuit breaker state persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001) with composite key
    ``(pair_key_a, pair_key_b)`` per D8. Bespoke per D7:
    :meth:`load_all` returns every row in one call for circuit-breaker
    rehydration at start, which dominates over paginated walks for the
    expected cardinality of agent pairs.
    """

    @override
    async def save(self, entity: CircuitBreakerStateRecord) -> None:
        """Persist a circuit breaker state record (upsert).

        Args:
            entity: The state record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(
        self, entity_id: CircuitBreakerPairKey
    ) -> CircuitBreakerStateRecord | None:
        """Retrieve a circuit breaker state record by pair key.

        Args:
            entity_id: ``(pair_key_a, pair_key_b)`` tuple.

        Returns:
            The record, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        """List records in ``(pair_key_a, pair_key_b)`` order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated records ordered by composite key ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def load_all(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        """Load a bounded page of records (bespoke per ADR-0001 D7).

        Used by the circuit breaker guard to rehydrate every pair's
        state at start; cardinality scales with active agent pairs.
        The query is bounded per call (no unbounded scan); callers
        that need the complete set drain via
        :func:`synthorg.persistence._shared.collect_all`. Rows are in
        ``(pair_key_a, pair_key_b)`` order so paging is stable.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of stored records in deterministic key order.

        Raises:
            PersistenceError: If the query fails.
        """
        ...

    @override
    async def delete(self, entity_id: CircuitBreakerPairKey) -> bool:
        """Delete a circuit breaker state record.

        Args:
            entity_id: ``(pair_key_a, pair_key_b)`` tuple.

        Returns:
            True if a record was deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
