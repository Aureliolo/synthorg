# module-kind: declarative
"""Repository protocol for the durable active-principle store.

An :class:`ActivePrinciple` row is a constitutional principle applied by the
self-improvement meta-loop (prompt-tuning altitude) and read during prompt
assembly. The repository composes only the generic :class:`IdKeyedRepository`
surface (ADR-0001) keyed by the principle's ``id`` (canonical string form):
``save`` (upsert), ``get``, ``delete`` (rollback restore), and ``list_items``
(snapshot load for the cached read provider). No bespoke methods: active
principles are low-cardinality org policy, so scope filtering happens in the
cached provider rather than a per-scope query.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``). All
protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import ActivePrinciple
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class ActivePrincipleRepository(
    IdKeyedRepository[ActivePrinciple, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for durable active principles, keyed by ``str(id)``.

    ``save`` is an upsert on the principle id. ``list_items`` returns every
    active principle (newest-first) so the cached provider can build its
    in-memory snapshot at boot and after applier writes. No bespoke methods.

    Non-recoverable errors propagate as
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: ActivePrinciple, /) -> None:
        """Upsert an active principle keyed by ``str(id)``.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> ActivePrinciple | None:
        """Retrieve the active principle for ``str(id)``, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the active principle for ``str(id)``. ``True`` iff present.

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
    ) -> tuple[ActivePrinciple, ...]:
        """List active principles newest-first by ``created_at`` (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["ActivePrincipleRepository"]
