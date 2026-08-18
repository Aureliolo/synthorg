"""Task repository protocol."""

from typing import Protocol, Self, override, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.task import Task
from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class TaskFilterSpec(BaseModel):
    """Filter spec for ``TaskRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: TaskStatus | None = Field(
        default=None,
        description="Filter by task status",
    )
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Filter by assignee agent ID",
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Filter by project ID",
    )
    plan: UUID | None = Field(
        default=None,
        description="Filter by the plan whose dispatch created the task",
    )
    blocked_reason: BlockedReason | None = Field(
        default=None,
        description="Filter by why a blocked task is parked",
    )
    after_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Return only rows ordered after this id. Query ordering is"
            " deterministic on the primary key, so a caller walking a large"
            " set pages by carrying the last id forward instead of by offset:"
            " an offset re-counts every row it has already read, and skips a"
            " row outright when a concurrent writer removes one behind the"
            " window"
        ),
    )
    ids: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description=(
            "Return only these rows. A caller resolving a page's references"
            " asks once for the set rather than once per reference, which is"
            " the difference between one query and one per row"
        ),
    )

    @model_validator(mode="after")
    def _reject_an_empty_id_set(self) -> Self:
        """Refuse ``ids=()``, which reads as "no filter" and returns everything.

        Returns:
            ``self`` when the filter is expressible.

        Raises:
            ValueError: When ``ids`` is present but empty.
        """
        if self.ids is not None and not self.ids:
            msg = "ids must name at least one row; use None for no id filter"
            raise ValueError(msg)
        return self


@runtime_checkable
class TaskRepository(
    IdKeyedRepository[Task, NotBlankStr],
    FilteredQueryRepository[Task, TaskFilterSpec],
    Protocol,
):
    """CRUD + query interface for Task persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). One bespoke method, :meth:`save_many`, is justified under
    ADR-0001 D7 as a real performance optimisation (atomic batch upsert).
    """

    @override
    async def save(self, entity: Task, /) -> None:
        """Persist a task (insert or update by id).

        Args:
            entity: The task to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def save_many(self, entities: tuple[Task, ...], /) -> None:
        """Persist many tasks in one transaction (ADR-0001 D7).

        Upserts every task by id inside a single transaction, so a bulk
        reassignment commits atomically and in one round trip instead of
        N sequential ``save`` calls. An empty input is a no-op.

        Args:
            entities: The tasks to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> Task | None:
        """Retrieve a task by its ID.

        Args:
            entity_id: The task identifier.

        Returns:
            The task, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Tasks ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: TaskFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks matching the filter spec.

        Args:
            filter_spec: Carries optional filters for status, assigned_to,
                project, plan, and blocked_reason.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching tasks ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: TaskFilterSpec) -> int:
        """Count tasks matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching tasks.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a task by ID.

        Args:
            entity_id: The task identifier.

        Returns:
            ``True`` if the task was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
