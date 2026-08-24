"""Conformance tests for ``SprintRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is built over
the migrated ``backend.get_db()`` handle.

Covers:

* CRUD round-trip (save / get / list / delete) including tuple-valued
  ``task_ids`` / ``completed_task_ids``, the nullable ``project``, and
  the ISO date strings.
* In-place edit (re-save) round-trip and duplicate-id upsert.
* Filtered query by project / status, plus ``count`` agreement.
* Transition state machine: the strictly-linear lifecycle walk
  ``planning -> active -> in_review -> retrospective -> completed``;
  state mismatch returns ``False``.
* Unknown update keys on ``transition_if`` raise :class:`QueryError`.
* ``complete_task_if`` and ``add_task_if_planning``: the two guarded
  backlog writes, including the lost-update case a whole-entity ``save``
  cannot survive, and the derived story-point totals that make the last
  completion of a sprint impossible to refuse.
* One non-completed sprint per scope, enforced by the partial unique
  index, for both the per-project and the org-wide scope.

Every scenario that needs two sprints for one scope completes the first
one, because an open pair is exactly what the database now refuses.
"""

import asyncio
from typing import ClassVar, cast

import aiosqlite
import pytest

from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    MalformedRowError,
    QueryError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import (
    STORY_POINTS_CEILING,
    Sprint,
    SprintStatus,
)
from synthorg.persistence.postgres.sprint_repo import PostgresSprintRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository
from synthorg.persistence.sqlite.sprint_repo import SQLiteSprintRepository

pytestmark = pytest.mark.integration

_START = "2026-05-22T12:00:00+00:00"
_END = "2026-06-05T12:00:00+00:00"

#: Backlog cap for the appends that are not about the cap. Well clear of
#: every fixture here, so a test asserting something else cannot start
#: passing because the guard declined for a reason it never meant to raise.
_ROOMY_CAP = 500


def _repo(backend: PersistenceBackend) -> SprintRepository:
    """Return a concrete sprint repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteSprintRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresSprintRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_sprint(  # noqa: PLR0913 -- test helper carries the sprint field set
    *,
    sprint_id: str = "sprint-1",
    project: str | None = "proj-x",
    sprint_number: int = 1,
    status: SprintStatus = SprintStatus.PLANNING,
    task_ids: tuple[str, ...] = ("task-a", "task-b"),
    completed_task_ids: tuple[str, ...] = (),
    task_points: dict[str, float] | None = None,
    story_points_committed: float = 8.0,
    story_points_completed: float = 0.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Sprint:
    return Sprint(
        id=NotBlankStr(sprint_id),
        project=NotBlankStr(project) if project is not None else None,
        name=NotBlankStr("Sprint One"),
        goal="Ship the memory layer",
        status=status,
        sprint_number=sprint_number,
        duration_days=14,
        start_date=start_date,
        end_date=end_date,
        task_ids=tuple(NotBlankStr(t) for t in task_ids),
        completed_task_ids=tuple(NotBlankStr(t) for t in completed_task_ids),
        task_points=(
            task_points if task_points is not None else {"task-a": 5.0, "task-b": 3.0}
        ),
        story_points_committed=story_points_committed,
        story_points_completed=story_points_completed,
    )


def _completed_sprint(*, sprint_id: str, project: str | None, number: int) -> Sprint:
    """Build a COMPLETED sprint, the only kind that frees its scope.

    Returns:
        A terminal sprint for *project*, so a second sprint in the same
        scope is admissible.
    """
    return _make_sprint(
        sprint_id=sprint_id,
        project=project,
        sprint_number=number,
        status=SprintStatus.COMPLETED,
        start_date=_START,
        end_date=_END,
    )


def _open_sprint(
    *,
    sprint_id: str,
    project: str | None = "proj-x",
    number: int = 1,
    task_ids: tuple[str, ...] = ("task-a", "task-b"),
    completed_task_ids: tuple[str, ...] = (),
    story_points_completed: float = 0.0,
) -> Sprint:
    """Build an ACTIVE sprint, the state in which completions are accepted.

    Returns:
        An ACTIVE sprint carrying the supplied backlog.
    """
    return _make_sprint(
        sprint_id=sprint_id,
        project=project,
        sprint_number=number,
        status=SprintStatus.ACTIVE,
        task_ids=task_ids,
        completed_task_ids=completed_task_ids,
        story_points_completed=story_points_completed,
        start_date=_START,
    )


class TestSprintRepository:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        sprint = _make_sprint()
        await repo.save(sprint)

        fetched = await repo.get(NotBlankStr("sprint-1"))
        assert fetched is not None
        assert fetched.id == "sprint-1"
        assert fetched.project == "proj-x"
        assert fetched.status is SprintStatus.PLANNING
        assert fetched.goal == "Ship the memory layer"
        assert fetched.sprint_number == 1
        assert fetched.duration_days == 14
        assert fetched.task_ids == ("task-a", "task-b")
        assert fetched.completed_task_ids == ()
        assert dict(fetched.task_points) == {"task-a": 5.0, "task-b": 3.0}
        assert fetched.story_points_committed == pytest.approx(8.0)
        assert fetched.story_points_completed == pytest.approx(0.0)

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("missing")) is None

    async def test_null_project_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="org-wide", project=None))
        fetched = await repo.get(NotBlankStr("org-wide"))
        assert fetched is not None
        assert fetched.project is None

    async def test_active_sprint_with_dates_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        sprint = _make_sprint(
            sprint_id="s-active",
            status=SprintStatus.ACTIVE,
            start_date=_START,
        )
        await repo.save(sprint)
        fetched = await repo.get(NotBlankStr("s-active"))
        assert fetched is not None
        assert fetched.status is SprintStatus.ACTIVE
        assert fetched.start_date == _START

    async def test_edit_in_place_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        sprint = _make_sprint()
        await repo.save(sprint)

        edited = sprint.model_copy(
            update={
                "task_ids": ("task-a", "task-b", "task-c"),
                "story_points_committed": 13.0,
            }
        )
        await repo.save(edited)

        fetched = await repo.get(NotBlankStr("sprint-1"))
        assert fetched is not None
        assert fetched.task_ids == ("task-a", "task-b", "task-c")
        assert fetched.story_points_committed == pytest.approx(13.0)

    async def test_duplicate_id_save_is_upsert(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="dup"))
        await repo.save(
            _make_sprint(sprint_id="dup").model_copy(
                update={"name": NotBlankStr("Renamed")}
            )
        )
        fetched = await repo.get(NotBlankStr("dup"))
        assert fetched is not None
        assert fetched.name == "Renamed"

    async def test_query_by_project(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="p1", project="alpha", sprint_number=1))
        await repo.save(_make_sprint(sprint_id="p2", project="beta", sprint_number=1))

        alpha = await repo.query(SprintFilterSpec(project="alpha"))
        assert [s.id for s in alpha] == ["p1"]

    async def test_query_by_status(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        # Different projects: one open sprint per scope is a database rule
        # now, so two open rows have to belong to two scopes.
        await repo.save(_make_sprint(sprint_id="q1", project="q-plan"))
        await repo.save(
            _make_sprint(
                sprint_id="q2",
                project="q-active",
                status=SprintStatus.ACTIVE,
                start_date=_START,
            )
        )

        planning = await repo.query(SprintFilterSpec(status=SprintStatus.PLANNING))
        assert {s.id for s in planning} >= {"q1"}
        assert "q2" not in {s.id for s in planning}

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        # A project accumulates sprints over time, so its history is one
        # completed row plus the open one that succeeded it.
        await repo.save(_completed_sprint(sprint_id="c1", project="gamma", number=1))
        await repo.save(_make_sprint(sprint_id="c2", project="gamma", sprint_number=2))
        await repo.save(_make_sprint(sprint_id="c3", project="delta", sprint_number=1))

        spec = SprintFilterSpec(project="gamma")
        assert await repo.count(spec) == len(await repo.query(spec))
        assert await repo.count(spec) == 2

    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_completed_sprint(sprint_id="l1", project="p", number=1))
        await repo.save(_make_sprint(sprint_id="l2", project="p", sprint_number=2))

        rows = await repo.list_items()
        ids = [r.id for r in rows]
        assert ids.index("l2") < ids.index("l1")

    async def test_full_lifecycle_walk(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="walk"))

        assert await repo.transition_if(
            NotBlankStr("walk"),
            SprintStatus.PLANNING,
            SprintStatus.ACTIVE,
            start_date=_START,
        )
        assert await repo.transition_if(
            NotBlankStr("walk"), SprintStatus.ACTIVE, SprintStatus.IN_REVIEW
        )
        assert await repo.transition_if(
            NotBlankStr("walk"),
            SprintStatus.IN_REVIEW,
            SprintStatus.RETROSPECTIVE,
        )
        assert await repo.transition_if(
            NotBlankStr("walk"),
            SprintStatus.RETROSPECTIVE,
            SprintStatus.COMPLETED,
            end_date=_END,
        )

        fetched = await repo.get(NotBlankStr("walk"))
        assert fetched is not None
        assert fetched.status is SprintStatus.COMPLETED
        assert fetched.start_date == _START
        assert fetched.end_date == _END

    async def test_transition_returns_false_on_state_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="mm"))
        # Sprint is PLANNING; an ACTIVE -> IN_REVIEW CAS must not match.
        assert (
            await repo.transition_if(
                NotBlankStr("mm"), SprintStatus.ACTIVE, SprintStatus.IN_REVIEW
            )
            is False
        )

    async def test_transition_rejects_unknown_update_key(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="uk"))
        with pytest.raises(QueryError, match="unknown update keys"):
            await repo.transition_if(
                NotBlankStr("uk"),
                SprintStatus.PLANNING,
                SprintStatus.ACTIVE,
                start_date=_START,
                bogus_key="nope",
            )

    async def test_task_points_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="tp",
                task_ids=("task-a", "task-b", "task-c"),
                task_points={"task-a": 1.5, "task-b": 2.0, "task-c": 4.5},
                story_points_committed=8.0,
            )
        )
        fetched = await repo.get(NotBlankStr("tp"))
        assert fetched is not None
        assert dict(fetched.task_points) == {
            "task-a": 1.5,
            "task-b": 2.0,
            "task-c": 4.5,
        }

    async def test_unique_project_sprint_number(
        self, backend: PersistenceBackend
    ) -> None:
        # The first sprint is COMPLETED so the one-open-per-scope index is
        # satisfied and the number collision is the only thing left to fail
        # on; an open pair would raise for the other reason.
        repo = _repo(backend)
        await repo.save(_completed_sprint(sprint_id="u1", project="proj-u", number=1))
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_sprint(sprint_id="u2", project="proj-u", sprint_number=1)
            )

    async def test_unique_org_wide_sprint_number(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_completed_sprint(sprint_id="ow1", project=None, number=1))
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_sprint(sprint_id="ow2", project=None, sprint_number=1)
            )

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint())
        assert await repo.delete(NotBlankStr("sprint-1")) is True
        assert await repo.get(NotBlankStr("sprint-1")) is None

    async def test_delete_returns_false_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.delete(NotBlankStr("nope")) is False


class TestCompleteTaskIf:
    """The guarded backlog append.

    ``save`` cannot express this: it writes the whole entity, so a caller
    holding a stale pre-image overwrites whatever landed in between. Every
    case here is about what the guard refuses.
    """

    async def test_appends_and_credits_points(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-1"))

        post = await repo.complete_task_if(NotBlankStr("ct-1"), NotBlankStr("task-a"))

        assert post is not None
        assert post.completed_task_ids == ("task-a",)
        assert post.story_points_completed == pytest.approx(5.0)
        assert post.task_ids == ("task-a", "task-b")

    async def test_second_call_for_one_task_is_a_no_op(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-2"))
        await repo.complete_task_if(NotBlankStr("ct-2"), NotBlankStr("task-a"))

        assert (
            await repo.complete_task_if(NotBlankStr("ct-2"), NotBlankStr("task-a"))
            is None
        )

        fetched = await repo.get(NotBlankStr("ct-2"))
        assert fetched is not None
        assert fetched.completed_task_ids == ("task-a",)
        assert fetched.story_points_completed == pytest.approx(5.0)

    async def test_concurrent_completions_of_different_tasks_both_land(
        self, backend: PersistenceBackend
    ) -> None:
        """The lost-update regression this whole change exists for.

        Dispatched TOGETHER through ``asyncio.gather`` rather than in
        series: awaited one after the other, a single guarded UPDATE cannot
        lose an update by construction, so a sequential pair proves the
        primitive composes and nothing about a race. Together, the two calls
        genuinely contend, which is what exercises SQLite's write lock and
        Postgres' re-evaluation of the WHERE against the newer row version.

        Both callers hold the same pre-image, as two processes handling two
        completions in the same window do. Under the previous
        read-modify-``save`` path the second write clobbered the first and
        one task's completion was gone permanently.
        """
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-3"))
        pre = await repo.get(NotBlankStr("ct-3"))
        assert pre is not None
        assert pre.completed_task_ids == ()

        first, second = await asyncio.gather(
            repo.complete_task_if(NotBlankStr("ct-3"), NotBlankStr("task-a")),
            repo.complete_task_if(NotBlankStr("ct-3"), NotBlankStr("task-b")),
        )

        assert first is not None
        assert second is not None
        fetched = await repo.get(NotBlankStr("ct-3"))
        assert fetched is not None
        assert set(fetched.completed_task_ids) == {"task-a", "task-b"}
        assert fetched.story_points_completed == pytest.approx(8.0)

    async def test_concurrent_completions_of_one_task_credit_it_once(
        self, backend: PersistenceBackend
    ) -> None:
        """Contending on the SAME task: exactly one call wins.

        The complement of the case above. Two duplicate completion events
        for one task arriving together must not double-credit it, and the
        loser must be told "nothing was written" rather than raise.
        """
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-7"))

        results = await asyncio.gather(
            repo.complete_task_if(NotBlankStr("ct-7"), NotBlankStr("task-a")),
            repo.complete_task_if(NotBlankStr("ct-7"), NotBlankStr("task-a")),
        )

        assert sum(1 for r in results if r is not None) == 1
        fetched = await repo.get(NotBlankStr("ct-7"))
        assert fetched is not None
        assert fetched.completed_task_ids == ("task-a",)
        assert fetched.story_points_completed == pytest.approx(5.0)

    async def test_no_completion_can_violate_the_points_invariant(
        self, backend: PersistenceBackend
    ) -> None:
        """The row's own points CHECK is unreachable from this statement.

        The protocol still declares ``ConstraintViolationError`` because a
        refusal by the ROW is a different answer from a refusal by the
        GUARD and the caller treats them differently: ``None`` means
        another writer got there and the sprint is fine, while a raise
        means the write is inadmissible. What this asserts is that the
        completion path can no longer produce the latter, which is what the
        clamped, re-derived total buys: the credited value is pinned to the
        committed one from below and cannot be pushed past it, whatever the
        row already carried.
        """
        repo = _repo(backend)
        # A pre-existing row whose committed total is already the smallest
        # value its backlog admits, so there is no headroom to overshoot.
        await repo.save(
            _make_sprint(
                sprint_id="ct-9",
                status=SprintStatus.ACTIVE,
                start_date=_START,
                task_ids=("task-a",),
                task_points={"task-a": 5.0},
                story_points_committed=5.0,
                story_points_completed=0.0,
            )
        )

        post = await repo.complete_task_if(NotBlankStr("ct-9"), NotBlankStr("task-a"))

        assert post is not None
        assert post.story_points_completed == pytest.approx(5.0)
        assert post.story_points_completed <= post.story_points_committed

    async def test_refuses_task_outside_the_backlog(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-4"))

        assert (
            await repo.complete_task_if(
                NotBlankStr("ct-4"), NotBlankStr("task-elsewhere")
            )
            is None
        )

        fetched = await repo.get(NotBlankStr("ct-4"))
        assert fetched is not None
        assert fetched.completed_task_ids == ()
        assert fetched.story_points_completed == pytest.approx(0.0)

    @pytest.mark.parametrize(
        ("status", "start_date", "end_date"),
        [
            (SprintStatus.PLANNING, None, None),
            (SprintStatus.RETROSPECTIVE, _START, None),
            (SprintStatus.COMPLETED, _START, _END),
        ],
    )
    async def test_refuses_a_sprint_that_is_not_open(
        self,
        backend: PersistenceBackend,
        status: SprintStatus,
        start_date: str | None,
        end_date: str | None,
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="ct-5",
                status=status,
                start_date=start_date,
                end_date=end_date,
            )
        )

        assert (
            await repo.complete_task_if(NotBlankStr("ct-5"), NotBlankStr("task-a"))
            is None
        )

        fetched = await repo.get(NotBlankStr("ct-5"))
        assert fetched is not None
        assert fetched.completed_task_ids == ()

    async def test_accepts_a_sprint_in_review(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="ct-6",
                status=SprintStatus.IN_REVIEW,
                start_date=_START,
            )
        )

        post = await repo.complete_task_if(NotBlankStr("ct-6"), NotBlankStr("task-a"))

        assert post is not None
        assert post.completed_task_ids == ("task-a",)

    async def test_returns_none_for_a_missing_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert (
            await repo.complete_task_if(
                NotBlankStr("no-such-sprint"), NotBlankStr("task-a")
            )
            is None
        )


class TestCompletionPointsAreDerived:
    """The totals are re-derived, never accumulated.

    Accumulating gave the row a second, independent addition order:
    ``story_points_committed`` is folded as tasks are added, so for
    non-dyadic values the two totals disagree by an ULP and the table's
    ``CHECK (story_points_completed <= story_points_committed)`` refuses the
    LAST completion of a sprint. Nothing re-fires a completion, so that
    refusal is permanent: the sprint can never read as delivered and its
    scope stays locked by the one-open-per-scope index.
    """

    #: Non-dyadic points whose fold order changes the total's last bit.
    _POINTS: ClassVar[dict[str, float]] = {
        "t0": 0.1,
        "t1": 0.2,
        "t2": 0.3,
        "t3": 0.7,
        "t4": 1.1,
        "t5": 2.3,
        "t6": 0.9,
        "t7": 1.7,
    }

    async def _seed(
        self, backend: PersistenceBackend, *, sprint_id: str, project: str = "proj-x"
    ) -> SprintRepository:
        """Assemble a PLANNING sprint holding every fractional task.

        Args:
            backend: The backend under test.
            sprint_id: The sprint to assemble.
            project: Its scope. A caller seeding a second sprint alongside
                a live one names a different scope, because the sprint
                this leaves ACTIVE holds the one it is given.

        Returns:
            The repository, with the sprint ACTIVE and ready to deliver.
        """
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id=sprint_id,
                project=project,
                task_ids=(),
                task_points={},
                story_points_committed=0.0,
            )
        )
        for task_id, points in self._POINTS.items():
            assert (
                await repo.add_task_if_planning(
                    NotBlankStr(sprint_id), NotBlankStr(task_id), points, _ROOMY_CAP
                )
                is not None
            )
        assert await repo.transition_if(
            NotBlankStr(sprint_id),
            SprintStatus.PLANNING,
            SprintStatus.ACTIVE,
            start_date=_START,
        )
        return repo

    @pytest.mark.parametrize(
        ("label", "order"),
        [
            ("ascending", ("t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7")),
            ("descending", ("t7", "t6", "t5", "t4", "t3", "t2", "t1", "t0")),
            ("interleaved", ("t3", "t0", "t5", "t1", "t7", "t2", "t6", "t4")),
        ],
    )
    async def test_last_completion_is_never_refused(
        self, backend: PersistenceBackend, label: str, order: tuple[str, ...]
    ) -> None:
        sprint_id = f"pts-{label}"
        repo = await self._seed(backend, sprint_id=sprint_id)

        for task_id in order:
            assert (
                await repo.complete_task_if(
                    NotBlankStr(sprint_id), NotBlankStr(task_id)
                )
                is not None
            ), f"completion of {task_id!r} was refused"

        fetched = await repo.get(NotBlankStr(sprint_id))
        assert fetched is not None
        assert len(fetched.completed_task_ids) == len(self._POINTS)
        assert fetched.story_points_completed == fetched.story_points_committed

    async def test_partial_credit_does_not_depend_on_order(
        self, backend: PersistenceBackend
    ) -> None:
        """Two folds of the same points, credited in opposite orders.

        Each sprint takes its own scope: the comparison needs two live
        sprints at once, and a scope admits one.
        """
        first = await self._seed(backend, sprint_id="pts-p1", project="pts-one")
        await first.complete_task_if(NotBlankStr("pts-p1"), NotBlankStr("t0"))
        await first.complete_task_if(NotBlankStr("pts-p1"), NotBlankStr("t4"))
        second = await self._seed(backend, sprint_id="pts-p2", project="pts-two")
        await second.complete_task_if(NotBlankStr("pts-p2"), NotBlankStr("t4"))
        await second.complete_task_if(NotBlankStr("pts-p2"), NotBlankStr("t0"))

        one = await first.get(NotBlankStr("pts-p1"))
        two = await second.get(NotBlankStr("pts-p2"))
        assert one is not None
        assert two is not None
        assert one.story_points_completed == two.story_points_completed


class TestAddTaskIfPlanning:
    """The guarded backlog assembly.

    The same lost-update shape as completion, one state earlier: two
    requests adding different tasks read one pre-image, and a whole-entity
    ``save`` lets the second write a backlog that never saw the first.
    """

    async def test_appends_and_totals_the_backlog(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="at-1",
                task_ids=(),
                task_points={},
                story_points_committed=0.0,
            )
        )

        post = await repo.add_task_if_planning(
            NotBlankStr("at-1"), NotBlankStr("task-new"), 4.0, _ROOMY_CAP
        )

        assert post is not None
        assert post.task_ids == ("task-new",)
        assert post.task_points == {"task-new": 4.0}
        assert post.story_points_committed == pytest.approx(4.0)

    async def test_concurrent_adds_of_different_tasks_contend_and_both_land(
        self, backend: PersistenceBackend
    ) -> None:
        """The lost-update regression on the assembly write.

        Dispatched TOGETHER through ``asyncio.gather`` for the reason
        ``test_concurrent_completions_of_different_tasks_both_land``
        states: awaited in series, one guarded UPDATE cannot lose an
        update by construction, so a sequential pair proves the primitive
        composes and nothing about a race. Under a whole-entity ``save``
        the second wrote a backlog assembled from a pre-image that never
        saw the first.
        """
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="at-2",
                task_ids=(),
                task_points={},
                story_points_committed=0.0,
            )
        )
        pre = await repo.get(NotBlankStr("at-2"))
        assert pre is not None
        assert pre.task_ids == ()

        await asyncio.gather(
            repo.add_task_if_planning(
                NotBlankStr("at-2"), NotBlankStr("task-p"), 2.0, _ROOMY_CAP
            ),
            repo.add_task_if_planning(
                NotBlankStr("at-2"), NotBlankStr("task-q"), 3.0, _ROOMY_CAP
            ),
        )

        fetched = await repo.get(NotBlankStr("at-2"))
        assert fetched is not None
        assert set(fetched.task_ids) == {"task-p", "task-q"}
        assert fetched.story_points_committed == pytest.approx(5.0)

    async def test_a_full_backlog_declines_the_append(
        self, backend: PersistenceBackend
    ) -> None:
        """The cap is the statement's, not the caller's.

        ``_make_sprint`` seeds two tasks, so a cap of two is already
        reached and the guard matches nothing.
        """
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="at-cap"))

        assert (
            await repo.add_task_if_planning(
                NotBlankStr("at-cap"), NotBlankStr("task-c"), 1.0, 2
            )
            is None
        )

        fetched = await repo.get(NotBlankStr("at-cap"))
        assert fetched is not None
        assert fetched.task_ids == ("task-a", "task-b")

    async def test_concurrent_adds_cannot_both_take_the_last_slot(
        self, backend: PersistenceBackend
    ) -> None:
        """The race the cap moved into the guard to close.

        Two appends dispatched together onto a backlog with one slot
        left. Checked against a row each had already read, both find room
        and both land, and the cap is service configuration that no
        column CHECK holds, so the over-cap backlog is durable. Held in
        the statement, exactly one wins.
        """
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="at-race",
                task_ids=("task-a",),
                task_points={"task-a": 1.0},
                story_points_committed=1.0,
            )
        )

        results = await asyncio.gather(
            repo.add_task_if_planning(
                NotBlankStr("at-race"), NotBlankStr("task-p"), 1.0, 2
            ),
            repo.add_task_if_planning(
                NotBlankStr("at-race"), NotBlankStr("task-q"), 1.0, 2
            ),
        )

        assert sum(1 for r in results if r is not None) == 1
        fetched = await repo.get(NotBlankStr("at-race"))
        assert fetched is not None
        assert len(fetched.task_ids) == 2

    async def test_second_call_for_one_task_is_a_no_op(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="at-3"))

        assert (
            await repo.add_task_if_planning(
                NotBlankStr("at-3"), NotBlankStr("task-a"), 99.0, _ROOMY_CAP
            )
            is None
        )

        fetched = await repo.get(NotBlankStr("at-3"))
        assert fetched is not None
        assert fetched.task_ids == ("task-a", "task-b")
        assert fetched.story_points_committed == pytest.approx(8.0)

    @pytest.mark.parametrize(
        ("status", "start_date", "end_date"),
        [
            (SprintStatus.ACTIVE, _START, None),
            (SprintStatus.IN_REVIEW, _START, None),
            (SprintStatus.COMPLETED, _START, _END),
        ],
    )
    async def test_refuses_a_sprint_that_has_left_planning(
        self,
        backend: PersistenceBackend,
        status: SprintStatus,
        start_date: str | None,
        end_date: str | None,
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="at-4",
                status=status,
                start_date=start_date,
                end_date=end_date,
            )
        )

        assert (
            await repo.add_task_if_planning(
                NotBlankStr("at-4"), NotBlankStr("task-new"), 1.0, _ROOMY_CAP
            )
            is None
        )

        fetched = await repo.get(NotBlankStr("at-4"))
        assert fetched is not None
        assert fetched.task_ids == ("task-a", "task-b")
        assert fetched.status is status

    async def test_task_ids_carrying_json_metacharacters_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        # ``task_points`` is keyed by an arbitrary task id, so the key is
        # bound rather than concatenated into a JSON path: '$.' || id breaks
        # on a dot, a bracket or a quote, and would write the points under
        # some other key or fail outright.
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="at-5",
                task_ids=(),
                task_points={},
                story_points_committed=0.0,
            )
        )
        hostile = ("a.b", "x[0]", 'q"r', "$")
        for index, task_id in enumerate(hostile, start=1):
            assert (
                await repo.add_task_if_planning(
                    NotBlankStr("at-5"), NotBlankStr(task_id), float(index), _ROOMY_CAP
                )
                is not None
            )

        fetched = await repo.get(NotBlankStr("at-5"))
        assert fetched is not None
        assert fetched.task_ids == hostile
        assert dict(fetched.task_points) == {
            task_id: float(index) for index, task_id in enumerate(hostile, start=1)
        }
        assert fetched.story_points_committed == pytest.approx(10.0)

    async def test_returns_none_for_a_missing_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert (
            await repo.add_task_if_planning(
                NotBlankStr("no-such-sprint"), NotBlankStr("task-a"), 1.0, _ROOMY_CAP
            )
            is None
        )

    async def test_a_row_the_model_refuses_is_never_left_behind(
        self, backend: PersistenceBackend
    ) -> None:
        """A derived column can breach a bound the schema does not carry.

        ``story_points_committed`` is re-totalled in SQL and neither table
        bounds it, while ``Sprint`` caps it at ``STORY_POINTS_CEILING``.
        Committing before parsing made such a row durable AND unreadable
        in one step: `get`, `query`, `list_items` and the recovery sweep
        all failed on it afterwards, and the one-open-per-scope index kept
        the scope locked for good. The write must not stand.
        """
        repo = _repo(backend)
        await repo.save(
            _make_sprint(
                sprint_id="ceil-1",
                task_ids=("task-a",),
                task_points={"task-a": STORY_POINTS_CEILING},
                story_points_committed=STORY_POINTS_CEILING,
            )
        )

        with pytest.raises(MalformedRowError):
            await repo.add_task_if_planning(
                NotBlankStr("ceil-1"),
                NotBlankStr("task-over"),
                STORY_POINTS_CEILING,
                _ROOMY_CAP,
            )

        # The row is still there, still readable, still what it was.
        fetched = await repo.get(NotBlankStr("ceil-1"))
        assert fetched is not None
        assert fetched.task_ids == ("task-a",)
        assert fetched.story_points_committed == pytest.approx(STORY_POINTS_CEILING)
        # And the reads that a poisoned row used to break still work.
        assert await repo.count(SprintFilterSpec(project="proj-x")) == 1
        assert len(await repo.list_items()) == 1


class TestOneOpenSprintPerScope:
    """The partial unique index that decides the create race.

    The predicate matches ``SprintService``'s own "is anything open here"
    check exactly, so the database refuses what a check-then-act between
    two processes would otherwise wave through.
    """

    async def test_refuses_a_second_open_sprint_for_one_project(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_sprint(sprint_id="os-1", project="scoped", sprint_number=1)
        )
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_sprint(sprint_id="os-2", project="scoped", sprint_number=2)
            )

    async def test_refuses_a_second_open_org_wide_sprint(
        self, backend: PersistenceBackend
    ) -> None:
        """NULL project is its own scope, not an absence of one.

        Both engines treat NULLs as distinct in a unique index, so a bare
        ``(project)`` index would leave this pair admissible.
        """
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="ow-a", project=None, sprint_number=1))
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_sprint(sprint_id="ow-b", project=None, sprint_number=2)
            )

    async def test_allows_a_new_sprint_once_the_previous_completed(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_completed_sprint(sprint_id="seq-1", project="seq", number=1))
        await repo.save(_make_sprint(sprint_id="seq-2", project="seq", sprint_number=2))

        rows = await repo.query(SprintFilterSpec(project="seq"))
        assert {s.id for s in rows} == {"seq-1", "seq-2"}

    async def test_open_sprints_in_different_projects_coexist(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="mp-1", project="one"))
        await repo.save(_make_sprint(sprint_id="mp-2", project="two"))

        assert await repo.count(SprintFilterSpec()) == 2

    async def test_lifecycle_advance_does_not_trip_the_index(
        self, backend: PersistenceBackend
    ) -> None:
        """A hop between two non-completed statuses keeps the same key.

        The index is on the scope, not the status, so advancing must not
        collide with the row being advanced.
        """
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="adv", project="advancing"))

        assert await repo.transition_if(
            NotBlankStr("adv"),
            SprintStatus.PLANNING,
            SprintStatus.ACTIVE,
            start_date=_START,
        )


class TestOrgWideScopeFilter:
    """``project=None`` is "every project"; ``org_wide_only`` is the scope."""

    async def test_org_wide_only_returns_just_the_null_project_rows(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="sc-org", project=None))
        await repo.save(_make_sprint(sprint_id="sc-proj", project="named"))

        rows = await repo.query(SprintFilterSpec(org_wide_only=True))
        assert [s.id for s in rows] == ["sc-org"]
        assert await repo.count(SprintFilterSpec(org_wide_only=True)) == 1

    async def test_unset_project_still_matches_every_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="sc-org2", project=None))
        await repo.save(_make_sprint(sprint_id="sc-proj2", project="named"))

        rows = await repo.query(SprintFilterSpec())
        assert {s.id for s in rows} == {"sc-org2", "sc-proj2"}
