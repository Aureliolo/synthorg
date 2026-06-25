"""Runtime tool-call failure signal persistence protocol and model.

The decay accumulator for the runtime tool-call feedback loop
(:mod:`synthorg.providers.tool_call_feedback`). One row per
``(provider_name, model_id)`` holds a time-decayed failure score; when
the score crosses the configured threshold the model's
``ModelMetadata.tool_calls_verified`` is flipped to ``False`` via the
provider-management service. This lightweight per-model counter is kept
out of the provider-config JSON blob so a failure observation never
triggers a full registry hot-reload.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


class ModelToolCallSignal(BaseModel):
    """Time-decayed tool-call failure state for one ``(provider, model)``.

    Attributes:
        provider_name: SynthOrg provider registry key.
        model_id: Model identifier within the provider.
        failure_score: Exponentially time-decayed count of non-retryable
            tool-call failures. A genuine tool-call success zeroes it.
            When it crosses the configured threshold the model is
            downgraded.
        decayed_at: Epoch seconds (UTC wall clock) at which
            ``failure_score`` was last recomputed. Stored as a float (the
            same decay-arithmetic timing representation as
            ``CircuitBreakerStateRecord.opened_at``) so the next
            observation can decay the score forward across process
            restarts without an ISO round-trip.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Provider registry key")
    model_id: NotBlankStr = Field(description="Model identifier")
    failure_score: float = Field(
        ge=0.0,
        description="Time-decayed non-retryable tool-call failure count",
    )
    decayed_at: float = Field(
        description="Epoch seconds when failure_score was last recomputed",
    )


ModelToolCallSignalKey = tuple[NotBlankStr, NotBlankStr]
"""Composite primary key: ``(provider_name, model_id)``."""


@runtime_checkable
class ModelToolCallSignalRepository(
    IdKeyedRepository[ModelToolCallSignal, ModelToolCallSignalKey],
    Protocol,
):
    """CRUD interface for runtime tool-call failure signal persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001) with composite key
    ``(provider_name, model_id)`` per D8. No bespoke methods: the tracker
    hydrates lazily per key via :meth:`get` and clears via
    :meth:`delete`.
    """

    @override
    async def save(self, entity: ModelToolCallSignal, /) -> None:
        """Persist a signal record (upsert by composite key).

        Args:
            entity: The signal record to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(
        self, entity_id: ModelToolCallSignalKey, /
    ) -> ModelToolCallSignal | None:
        """Retrieve a signal record by ``(provider_name, model_id)``.

        Args:
            entity_id: ``(provider_name, model_id)`` tuple.

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
    ) -> tuple[ModelToolCallSignal, ...]:
        """List records in ``(provider_name, model_id)`` order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated records ordered by composite key ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: ModelToolCallSignalKey, /) -> bool:
        """Delete a signal record by ``(provider_name, model_id)``.

        Args:
            entity_id: ``(provider_name, model_id)`` tuple.

        Returns:
            ``True`` if a record was deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
