# module-kind: declarative
"""Repository protocol for the durable role registry.

A :class:`RoleRecord` row is a first-class role: the durable form of what was a
static ``BUILTIN_ROLES`` Python catalog plus free-string ``AgentIdentity.role``
attributes. The registry is seeded from ``BUILTIN_ROLES`` on first boot and is
the read / write surface for the architecture applier's ``create_role`` /
``remove_role`` operations. The repository composes only the generic
:class:`IdKeyedRepository` surface (ADR-0001) keyed by ``role.name``: ``save``
(upsert), ``get``, ``delete``, and ``list_items`` (rehydrate the in-memory
lookups + answer ``has_role`` / ``role_in_use``). No bespoke methods:
``role_in_use`` is computed in the applier context against the agent registry,
not the durable store.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``). All
protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class RoleRegistryRepository(
    IdKeyedRepository[RoleRecord, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for durable roles, keyed by ``role.name``.

    ``save`` is an upsert on the role name (the seed pass upserts each
    built-in once). ``list_items`` returns every role alphabetically so the
    registry can rebuild its in-memory lookups on restart. No bespoke methods.

    Non-recoverable errors propagate as
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: RoleRecord, /) -> None:
        """Upsert a role keyed by ``role.name``.

        Raises:
            QueryError: On database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> RoleRecord | None:
        """Retrieve the role for ``name``, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the role for ``name``. ``True`` iff present.

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
    ) -> tuple[RoleRecord, ...]:
        """List roles alphabetically by ``name`` (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["RoleRegistryRepository"]
