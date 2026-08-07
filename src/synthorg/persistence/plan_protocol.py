"""Plan repository protocol."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class PlanFilterSpec(BaseModel):
    """Filter spec for ``PlanRepository.query``.

    All fields are optional; an empty spec matches every plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: PlanStatus | None = Field(
        default=None,
        description="Filter by plan lifecycle status",
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Filter by project id",
    )
    objective_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by the charter/objective the plan serves",
    )
    parent_task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by the objective task the plan decomposes",
    )


@runtime_checkable
class PlanRepository(
    IdKeyedRepository[Plan, NotBlankStr],
    FilteredQueryRepository[Plan, PlanFilterSpec],
    Protocol,
):
    """CRUD + query interface for Plan persistence.

    Mirrors the project/task split: atomic ``create`` / ``update`` preserve
    distinct create-vs-update audit semantics, while ``save`` is the upsert
    convenience for migration / import paths. Composes
    :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`.
    """

    async def create(self, plan: Plan) -> None:
        """Insert a new plan, failing if the id already exists.

        Args:
            plan: The plan to insert.

        Raises:
            DuplicateRecordError: A plan with the same id is already
                persisted.
            QueryError: If the database operation fails.
        """
        ...

    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        """Update an existing plan, failing if no row matches.

        Args:
            plan: The plan to update; ``plan.id`` selects the row.
            expected_version: When set, an optimistic-concurrency guard: the
                write only lands if the stored row still carries this version,
                otherwise a :class:`PersistenceVersionConflictError` is raised.

        Raises:
            PersistenceVersionConflictError: ``expected_version`` was supplied
                and the stored version has moved (a concurrent write won).
            RecordNotFoundError: No plan with this id exists.
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def save(self, entity: Plan, /) -> None:
        """Persist a plan via upsert (insert or update by id).

        Used for migration / import paths that do not know whether the row
        exists; production CRUD uses :meth:`create` / :meth:`update`.

        Args:
            entity: The plan to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> Plan | None:
        """Retrieve a plan by its id.

        Args:
            entity_id: The plan identifier.

        Returns:
            The plan, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        """List all plans in id order.

        Args:
            limit: Maximum plans to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Plans in ascending id order, capped at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: PlanFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        """List plans matching the filter spec, ordered by id ascending.

        Args:
            filter_spec: Optional ``status`` / ``project`` /
                ``objective_id`` / ``parent_task_id`` filters.
            limit: Maximum plans to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching plans ordered by id, capped at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: PlanFilterSpec) -> int:
        """Count plans matching the filter spec."""
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a plan by id.

        Args:
            entity_id: The plan identifier.

        Returns:
            ``True`` if the plan was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...
