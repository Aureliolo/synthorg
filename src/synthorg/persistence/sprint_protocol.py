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

from typing import TYPE_CHECKING, Protocol, Self, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)

if TYPE_CHECKING:
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

    ``project`` and ``org_wide_only`` answer two different questions that
    a bare ``project=None`` cannot tell apart. Unset, ``project`` is the
    absence of a project predicate, so the spec matches every scope;
    ``org_wide_only`` narrows to the rows a ``Sprint`` model marks
    org-wide by carrying no project at all. Asking for both at once names
    two contradictory scopes and is refused rather than silently
    resolved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    project: NotBlankStr | None = Field(default=None)
    status: SprintStatus | None = Field(default=None)
    org_wide_only: bool = Field(
        default=False,
        description="Match only sprints with no owning project",
    )

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        """Reject a spec asking for one project and the org-wide scope.

        Returns:
            ``self`` unchanged when the requested scope is coherent.

        Raises:
            ValueError: When ``org_wide_only`` is paired with a project.
        """
        if self.org_wide_only and self.project is not None:
            msg = (
                "org_wide_only excludes every owned project, so it cannot "
                f"be combined with project={self.project!r}"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class SprintRepository(
    StatefulRepository[Sprint, NotBlankStr, SprintStatus],
    FilteredQueryRepository[Sprint, SprintFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for agile sprints.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Every write a running sprint takes is guarded, because
    each of them is a read-modify-write that two processes can enter at
    once: the linear lifecycle hops go through :meth:`transition_if`, and
    the completion append goes through :meth:`complete_task_if`. The
    generic :meth:`save` upsert is left to sprint *assembly* (creation and
    ``PLANNING`` backlog edits), where the row is not yet contended and
    the partial unique index on the scope decides who creates it.

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
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the sprint lifecycle state.

        ``**updates`` MAY carry the date columns stamped at the
        transition (``start_date`` on activation, ``end_date`` on
        completion), per the :class:`TransitionKwargs` shape. Each is
        applied via ``COALESCE`` so a missing key leaves the column
        unchanged. It stays typed ``object`` rather than
        ``Unpack[TransitionKwargs]`` because this ``@runtime_checkable``
        protocol is signature-introspected by typeguard, which under
        PEP 649 evaluates the annotation at runtime where the
        ``TYPE_CHECKING``-only ``Unpack`` name is undefined.
        Implementations validate types at the boundary and reject
        unknown keys with :class:`QueryError`.

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

    async def complete_task_if(
        self,
        /,
        sprint_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> Sprint | None:
        """Append *task_id* to ``completed_task_ids`` iff it is absent.

        Bespoke under ADR-0001 D7 (a domain invariant callers must not
        bypass). :meth:`save` cannot express it: that writes the whole
        entity, so a caller holding a pre-image from before a concurrent
        completion overwrites it and one task's delivery is lost for
        good. This is one conditional statement, so the read and the
        write cannot be interleaved.

        The guard holds four things at once: the row exists, its status
        is one a completion is admissible in (``active`` / ``in_review``),
        *task_id* is in the backlog, and it is not already completed.

        ``story_points_completed`` is RE-DERIVED in the same statement as
        the sum of ``task_points`` over the resulting completed set,
        never accumulated. Accumulating it took a second, independent
        addition order: ``story_points_committed`` is summed in Python as
        tasks are added, so for non-dyadic floats the two totals differ
        by an ULP and the table's
        ``CHECK (story_points_completed <= story_points_committed)``
        refuses the LAST completion of a sprint. That refusal is
        unrecoverable by construction, because nothing re-fires a task's
        completion and the recovery sweep never re-derives delivery, so
        the sprint could never read as delivered and its scope stayed
        locked by the one-open-per-scope index. A derived value has no
        fold order to disagree about, and it also means no caller can
        supply points unrelated to what the task actually committed.

        Args:
            sprint_id: The sprint whose backlog is being marked.
            task_id: The delivered task.

        Returns:
            The sprint as it stands after the append, or ``None`` when
            the guard did not match and nothing was written. ``None``
            covers every non-match identically; callers that need to tell
            "already completed" from "not in this backlog" apart make
            that distinction before calling, since this is the
            cross-process backstop rather than the error surface.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    async def add_task_if_planning(
        self,
        /,
        sprint_id: NotBlankStr,
        task_id: NotBlankStr,
        story_points: float,
    ) -> Sprint | None:
        """Append *task_id* to the backlog iff the sprint is still PLANNING.

        Bespoke under ADR-0001 D7, and for the same reason as
        :meth:`complete_task_if`: assembling a backlog through
        :meth:`save` is a read-modify-write over the whole entity, so two
        requests that each add a different task race, and the second
        overwrites ``task_ids``, ``task_points`` and
        ``story_points_committed`` with a pre-image that never saw the
        first. One task then vanishes from the backlog silently.

        A sprint being assembled is not less contended than one being
        worked: it sits behind an HTTP endpoint any number of callers can
        reach. The status guard additionally stops an assembly write
        landing on a sprint that has since started, which would otherwise
        revert its status and wipe its ``start_date`` without the
        lifecycle machine ever seeing the hop.

        Args:
            sprint_id: The sprint whose backlog is being assembled.
            task_id: The task to add.
            story_points: What this task commits, added to
                ``story_points_committed`` and recorded per task in
                ``task_points`` so completion credits exactly what
                assembly committed.

        Returns:
            The sprint after the append, or ``None`` when the guard did
            not match: no such row, not PLANNING, or the task is already
            in the backlog.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...
