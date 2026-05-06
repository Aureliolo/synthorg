"""Circuit breaker state persistence protocol and model."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class CircuitBreakerStateRecord(BaseModel):
    """Persistent state for a single agent-pair circuit breaker.

    Attributes:
        pair_key_a: First agent ID (lexicographically smaller).
        pair_key_b: Second agent ID (lexicographically larger).
        bounce_count: Delegations since last reset.
        trip_count: Number of times the circuit has tripped.
        opened_at: Monotonic timestamp when opened, or ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    pair_key_a: NotBlankStr = Field(description="First agent ID (sorted)")
    pair_key_b: NotBlankStr = Field(description="Second agent ID (sorted)")
    bounce_count: int = Field(ge=0, description="Bounces since last reset")
    trip_count: int = Field(ge=0, description="Lifetime trip count")
    opened_at: float | None = Field(
        default=None,
        description="Monotonic timestamp when circuit opened",
    )


class CircuitBreakerStateRepository(Protocol):
    """CRUD interface for circuit breaker state persistence."""

    async def save(self, record: CircuitBreakerStateRecord) -> None:
        """Persist a circuit breaker state record (upsert).

        Args:
            record: The state record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def load_all(self) -> tuple[CircuitBreakerStateRecord, ...]:
        """Load all persisted circuit breaker state records.

        Returns:
            All stored records.

        Raises:
            PersistenceError: If the query fails.
        """
        ...

    async def delete(self, pair_key_a: str, pair_key_b: str) -> bool:
        """Delete a circuit breaker state record.

        Args:
            pair_key_a: First agent ID.
            pair_key_b: Second agent ID.

        Returns:
            True if a record was deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
