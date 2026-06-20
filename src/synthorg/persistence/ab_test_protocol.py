# module-kind: declarative
"""Repository protocol for durable A/B-test rollout records.

An :class:`AbTestRecord` row is the durable summary of one
:class:`~synthorg.meta.rollout.ab_test.ABTestRollout` execution, keyed
by the proposal id. The repository composes only the generic
:class:`IdKeyedRepository` surface (ADR-0001): ``save`` (upsert so a
running record is updated to its terminal status), ``get`` (backs
``GET /meta/ab-tests/{proposal_id}``), ``delete``, and ``list_items``
(backs ``GET /meta/ab-tests``, newest-first). No bespoke methods.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.meta.rollout.ab_models import AbTestRecord
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class AbTestRepository(
    IdKeyedRepository[AbTestRecord, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for durable A/B-test rollout records.

    Composes :class:`IdKeyedRepository` (ADR-0001) keyed by the
    proposal id. ``save`` is an upsert so a rollout that first writes a
    ``running`` record and later its terminal verdict replaces the same
    row. ``list_items`` orders newest-first (by ``created_at``
    descending) to match the read endpoint. No bespoke methods beyond
    the generic surface.

    Non-recoverable errors propagate as
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: AbTestRecord, /) -> None:
        """Upsert an A/B-test record keyed by proposal id.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> AbTestRecord | None:
        """Retrieve a record by proposal id, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a record by proposal id. ``True`` iff a row existed.

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
    ) -> tuple[AbTestRecord, ...]:
        """List records newest-first by ``created_at`` (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["AbTestRepository"]
