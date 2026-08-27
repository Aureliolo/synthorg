"""Plan repository protocol."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.decomposition_progress import DecompositionProgress
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


class PlanDeleteOutcome(BaseModel):
    """What a guarded plan delete did, and what stopped it.

    Attributes:
        deleted: Whether the plan row was removed.
        live_task_count: Non-terminal tasks found under the plan. Non-zero
            means the delete was refused because work is still building.
            Zero alongside ``deleted=False`` means no plan with that id was
            there to delete.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    deleted: bool = Field(description="Whether the plan row was removed")
    live_task_count: int = Field(
        default=0,
        ge=0,
        description="Non-terminal tasks found under the plan",
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

    async def delete_if_no_live_tasks(
        self,
        entity_id: NotBlankStr,
        /,
        *,
        terminal_statuses: frozenset[str],
    ) -> PlanDeleteOutcome:
        """Delete a plan only while nothing is still building under it.

        Bespoke under ADR-0001 D7 as a domain invariant callers must not
        bypass. Counting live tasks in one call and deleting in another
        leaves a window in which a task filed in between is stranded on a
        plan id that no longer resolves, so the count and the delete are one
        conditional statement rather than two decisions that can disagree.

        Args:
            entity_id: The plan identifier.
            terminal_statuses: The task status values that count as
                finished. Supplied by the caller because the task lifecycle
                belongs to the domain layer, and a status this layer does
                not recognise must read as live rather than quietly clear
                the way for a delete.

        Returns:
            The outcome, distinguishing a delete from a refusal (naming how
            many tasks are still live) and from a plan that was not there.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def record_decomposition_progress(
        self,
        parent_task_id: NotBlankStr,
        /,
        *,
        progress: DecompositionProgress,
    ) -> Plan | None:
        """Stamp how far a decomposition has got on the shell it is filling.

        Bespoke under ADR-0001 D7 for two reasons a generic read-then-update
        cannot satisfy. The status is a WRITE condition, not a read one: the
        shell is the only legitimate target, and a plan that leaves
        ``PLANNING`` between a read and a write would otherwise be overwritten
        with the snapshot that preceded it, reviving a failed plan and
        discarding the reason somebody recorded. And the write must be
        version-NEUTRAL: the decomposition ends by claiming the shell at the
        version it started from, so a progress line that bumped the version
        would fail the very write it exists to describe.

        One column, one statement, no version guard, and ``updated_at`` is
        left alone: this describes a run rather than editing a plan.

        The stamped plan comes back rather than a boolean because the caller
        announces the change to any page holding it open, and that
        announcement names the plan. The condition is a write condition, so
        which shell took the stamp is not knowable before the statement runs
        and a second read to find out could answer about a different row.

        Args:
            parent_task_id: The objective whose shell is being filled.
            progress: The snapshot to stamp.

        Returns:
            The shell as it now stands, or ``None`` when no ``PLANNING`` plan
            was there to take the stamp.

        Raises:
            QueryError: If the operation fails.
        """
        ...
