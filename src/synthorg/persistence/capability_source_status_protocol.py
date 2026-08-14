# module-kind: declarative
"""Repository protocol for per-source capability-ingest status.

One row per registered source: when it was last tried, when it last
worked, and why it stopped if it did. Kept apart from the scores because
the two answer different questions, and only this one can tell an operator
whether the evidence behind a rung is current or a month old and frozen.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository
from synthorg.providers.capability_sources.status import CapabilitySourceStatus


@runtime_checkable
class CapabilitySourceStatusRepository(
    IdKeyedRepository[CapabilitySourceStatus, NotBlankStr],
    Protocol,
):
    """CRUD on the per-source ingest record, keyed by source label.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`~synthorg.core.persistence_errors.ConstraintViolationError`;
    other database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: CapabilitySourceStatus, /) -> None:
        """Upsert one source's status by label.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> CapabilitySourceStatus | None:
        """Retrieve one source's status, or ``None`` when never attempted.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete one source's status. ``True`` iff a row existed.

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
    ) -> tuple[CapabilitySourceStatus, ...]:
        """List statuses ordered by source label ascending (paginated).

        Raises:
            QueryError: If the database query fails or pagination args are
                invalid.
        """
        ...


__all__ = ["CapabilitySourceStatusRepository"]
