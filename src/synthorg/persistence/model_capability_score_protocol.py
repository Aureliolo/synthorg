# module-kind: declarative
"""Repository protocol for externally-sourced model capability scores.

One row per ``(source_label, model_identifier, axis)``: what one source
measured about one model on one axis, with the ``as_of`` the source
published and the ``ingested_at`` this installation read it.

Rows are upserted and never bulk-deleted by a refresh. A feed that drops a
model, reshuffles its identifiers, or fails outright leaves that model's
last good row in place, ageing visibly through ``as_of``. Deleting on
refresh would mean a transient fetch failure silently un-grades a model,
which is the failure mode this layer exists to remove rather than relocate.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    BatchWriteRepository,
    IdKeyedRepository,
)
from synthorg.providers.capability_sources.models import (
    CapabilityScore,
    CapabilityScoreKey,
)


@runtime_checkable
class ModelCapabilityScoreRepository(
    IdKeyedRepository[CapabilityScore, CapabilityScoreKey],
    BatchWriteRepository[CapabilityScore],
    Protocol,
):
    """Composite-keyed CRUD plus all-or-nothing batch ingest.

    Composes :class:`IdKeyedRepository` (ADR-0001) with the composite key
    ``(source_label, model_identifier, axis)`` per D8, and
    :class:`BatchWriteRepository` for ingest.

    The batch surface is load-bearing rather than a convenience: a feed is
    parsed as a whole and must land as a whole. A half-applied ingest is a
    source describing half an old feed and half a new one, which reads as
    healthy and grades models on a mixture no operator can reconstruct.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`~synthorg.core.persistence_errors.ConstraintViolationError`;
    other database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: CapabilityScore, /) -> None:
        """Upsert one score by ``(source_label, model_identifier, axis)``.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. the
                0-100 score band or the axis CHECK).
            QueryError: On other database errors.
        """
        ...

    @override
    async def save_many(self, entities: tuple[CapabilityScore, ...], /) -> None:
        """Upsert a whole feed's scores in one transaction.

        All-or-nothing: any row failing rolls back the entire ingest, so a
        source is never left half-refreshed. An empty batch is a no-op,
        which is what a source that legitimately published nothing looks
        like; it must not be mistaken for a reason to clear the source.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: CapabilityScoreKey, /) -> CapabilityScore | None:
        """Retrieve one score by composite key, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: CapabilityScoreKey, /) -> bool:
        """Delete one score by composite key. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CapabilityScore, ...]:
        """List scores ordered by composite key ascending (paginated).

        Raises:
            QueryError: If the database query fails or pagination args are
                invalid.
        """
        ...


__all__ = ["ModelCapabilityScoreRepository"]
