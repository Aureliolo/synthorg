"""Repository protocol for project charters.

A :class:`ProjectCharter` row is the durable record of a charter
produced by the deep CEO interview. The repository composes
:class:`StatefulRepository` (atomic lifecycle transitions:
``drafted -> approved | cancelled``) and :class:`FilteredQueryRepository`
(lookup by status / project / creator / conversation, which the
controllers and dashboard need).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import CharterStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.models import ProjectCharter
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)


class CharterFilterSpec(BaseModel):
    """Filter spec for ``CharterRepository.query``.

    All fields optional; an empty spec matches every charter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: CharterStatus | None = Field(default=None)
    project_id: NotBlankStr | None = Field(default=None)
    created_by: NotBlankStr | None = Field(default=None)
    conversation_id: NotBlankStr | None = Field(default=None)


@runtime_checkable
class CharterRepository(
    StatefulRepository[ProjectCharter, NotBlankStr, CharterStatus],
    FilteredQueryRepository[ProjectCharter, CharterFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for project charters.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). No bespoke methods beyond the generic surface.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    async def save(self, entity: ProjectCharter) -> None:
        """Upsert a charter row keyed by ``id``.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> ProjectCharter | None:
        """Retrieve a charter by ``id``, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a charter by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """List charters, newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the charter lifecycle state.

        ``**updates`` MAY carry the column values stamped at the
        transition (e.g. ``updated_at``, ``approved_at``, ``approved_by``,
        ``forecast_id``, ``correlation_id``, ``task_id`` on approval).
        Implementations validate types at the boundary and reject
        unknown keys with :class:`QueryError`.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors or an invalid update key.
        """
        ...

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """Return charters matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    async def count(self, filter_spec: CharterFilterSpec) -> int:
        """Count charters matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
