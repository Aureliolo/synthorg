"""Repository protocol for agile sprints.

A :class:`Sprint` row is the durable record of a time-boxed work cycle
for an ``agile_kanban`` project. The repository composes
:class:`StatefulRepository` (atomic lifecycle transitions across the
strictly-linear ``planning -> active -> in_review -> retrospective ->
completed`` state machine) and :class:`FilteredQueryRepository` (lookup
by project / status, which the service layer and dashboard need).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)

if TYPE_CHECKING:
    from typing import Unpack

    from typing_extensions import TypedDict

    class TransitionKwargs(TypedDict, total=False):
        """Typed kwargs for :meth:`SprintRepository.transition_if`.

        The lifecycle date columns stamped atomically with a compare-and-set:
        ``start_date`` on the ``planning -> active`` hop, ``end_date`` on the
        ``retrospective -> completed`` hop. Both are ISO-8601 ``str`` values;
        each is applied via ``COALESCE`` so an omitted key leaves its column
        untouched.
        """

        start_date: object
        end_date: object


class SprintFilterSpec(BaseModel):
    """Filter spec for ``SprintRepository.query``.

    All fields optional; an empty spec matches every sprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    project: NotBlankStr | None = Field(default=None)
    status: SprintStatus | None = Field(default=None)


@runtime_checkable
class SprintRepository(
    StatefulRepository[Sprint, NotBlankStr, SprintStatus],
    FilteredQueryRepository[Sprint, SprintFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for agile sprints.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Backlog mutations during ``PLANNING`` / ``ACTIVE`` (task
    ids, story points) go through the generic :meth:`save` upsert; the
    linear lifecycle hops go through :meth:`transition_if` so two
    concurrent completions cannot both advance the same sprint.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: Sprint, /) -> None:
        """Upsert a sprint row keyed by ``id``.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> Sprint | None:
        """Retrieve a sprint by ``id``, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a sprint by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """List sprints, newest-first (``sprint_number DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        /,
        entity_id: NotBlankStr,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: Unpack[TransitionKwargs],
    ) -> bool:
        """Atomic compare-and-set for the sprint lifecycle state.

        ``**updates`` MAY carry the date columns stamped at the
        transition (``start_date`` on activation, ``end_date`` on
        completion). Each is applied via ``COALESCE`` so a missing key
        leaves the column unchanged. Implementations validate types at
        the boundary and reject unknown keys with :class:`QueryError`.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors or an invalid update key.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: SprintFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """Return sprints matching the spec, newest-first (paginated).

        Order is ``(sprint_number DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: SprintFilterSpec) -> int:
        """Count sprints matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
