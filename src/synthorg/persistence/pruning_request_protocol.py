# module-kind: declarative
"""Repository protocol for durable pending HR-pruning requests.

A :class:`PruningRequest` row is the durable form of the pruning
service's in-memory ``_pending_requests`` entry. The repository composes
only the generic :class:`IdKeyedRepository` surface (ADR-0001) keyed by
``agent_id`` (one pending request per agent, the service invariant):
``save`` (upsert), ``get``, ``delete`` (pop on completion / rejection),
and ``list_items`` (rehydrate the in-memory cache on restart). No bespoke
methods.

The lookup key is ``agent_id`` (a ``NotBlankStr``), NOT the entity's
``.id`` UUID -- the service indexes pending requests by agent, so the
agent id is both the primary key and the lookup key.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.hr.pruning.models import PruningRequest
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class PruningRequestRepository(
    IdKeyedRepository[PruningRequest, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for pending HR-pruning requests, keyed by agent id.

    ``save`` is an upsert keyed on ``agent_id`` so a re-submitted request
    for the same agent replaces the prior pending row. ``list_items``
    returns every pending request (oldest-first) so the service can
    rehydrate ``_pending_requests`` on restart. No bespoke methods.

    Non-recoverable errors propagate as
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: PruningRequest, /) -> None:
        """Upsert a pending pruning request keyed by ``agent_id``.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> PruningRequest | None:
        """Retrieve the pending request for ``agent_id``, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the pending request for ``agent_id``. ``True`` iff present.

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
    ) -> tuple[PruningRequest, ...]:
        """List pending requests oldest-first by ``created_at`` (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["PruningRequestRepository"]
