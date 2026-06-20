# module-kind: declarative
"""Repository protocol for the durable department store.

A :class:`DepartmentRecord` row is the durable form of a department the
:class:`~synthorg.organization.services.DepartmentService` previously held only
in memory. The repository composes the generic :class:`IdKeyedRepository`
surface (ADR-0001) keyed by ``str(id)`` plus one bespoke read,
:meth:`get_by_name`, justified under ADR-0001 D7: the service enforces a
unique-name domain invariant (a created department must not collide with an
existing name) and the architecture applier keys departments by name, so a
name lookup that callers must not bypass with a full table scan is required.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``). All
protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.organization.department_record import DepartmentRecord
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class DepartmentRepository(
    IdKeyedRepository[DepartmentRecord, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for durable departments, keyed by ``str(id)``.

    ``save`` upserts on the department id. ``list_items`` returns departments
    newest-first so the service can rehydrate on restart. :meth:`get_by_name`
    backs the unique-name invariant and the applier's name-keyed lookups.

    Non-recoverable errors propagate as
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: DepartmentRecord, /) -> None:
        """Upsert a department keyed by ``str(id)``.

        Raises:
            QueryError: On database errors (including a name collision).
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> DepartmentRecord | None:
        """Retrieve the department for ``str(id)``, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the department for ``str(id)``. ``True`` iff present.

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
    ) -> tuple[DepartmentRecord, ...]:
        """List departments newest-first by ``created_at`` (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    async def get_by_name(self, name: NotBlankStr, /) -> DepartmentRecord | None:
        """Retrieve the department with ``name``, or ``None``.

        Backs the unique-name invariant and the architecture applier's
        name-keyed lookups (ADR-0001 D7 bespoke read).

        Raises:
            QueryError: If the database query fails.
        """
        ...


__all__ = ["DepartmentRepository"]
