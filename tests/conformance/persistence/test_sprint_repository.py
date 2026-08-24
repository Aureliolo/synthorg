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
* ``complete_task_if``: the guarded backlog append, including the
  lost-update case a whole-entity ``save`` cannot survive.
* One non-completed sprint per scope, enforced by the partial unique
  index, for both the per-project and the org-wide scope.

Every scenario that needs two sprints for one scope completes the first
one, because an open pair is exactly what the database now refuses.
"""

from typing import cast

import aiosqlite
import pytest

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.persistence.postgres.sprint_repo import PostgresSprintRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository
from synthorg.persistence.sqlite.sprint_repo import SQLiteSprintRepository

pytestmark = pytest.mark.integration

_START = "2026-05-22T12:00:00+00:00"
_END = "2026-06-05T12:00:00+00:00"


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

        post = await repo.complete_task_if(
            NotBlankStr("ct-1"), NotBlankStr("task-a"), 5.0
        )

        assert post is not None
        assert post.completed_task_ids == ("task-a",)
        assert post.story_points_completed == pytest.approx(5.0)
        assert post.task_ids == ("task-a", "task-b")

    async def test_second_call_for_one_task_is_a_no_op(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-2"))
        await repo.complete_task_if(NotBlankStr("ct-2"), NotBlankStr("task-a"), 5.0)

        assert (
            await repo.complete_task_if(NotBlankStr("ct-2"), NotBlankStr("task-a"), 5.0)
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

        Both callers hold the same pre-image, as two processes handling
        two completions in the same window do. Under the previous
        read-modify-``save`` path the second write clobbered the first and
        one task's completion was gone permanently.
        """
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-3"))
        pre = await repo.get(NotBlankStr("ct-3"))
        assert pre is not None
        assert pre.completed_task_ids == ()

        await repo.complete_task_if(NotBlankStr("ct-3"), NotBlankStr("task-a"), 5.0)
        await repo.complete_task_if(NotBlankStr("ct-3"), NotBlankStr("task-b"), 3.0)

        fetched = await repo.get(NotBlankStr("ct-3"))
        assert fetched is not None
        assert set(fetched.completed_task_ids) == {"task-a", "task-b"}
        assert fetched.story_points_completed == pytest.approx(8.0)

    async def test_refuses_task_outside_the_backlog(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_open_sprint(sprint_id="ct-4"))

        assert (
            await repo.complete_task_if(
                NotBlankStr("ct-4"), NotBlankStr("task-elsewhere"), 5.0
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
            await repo.complete_task_if(NotBlankStr("ct-5"), NotBlankStr("task-a"), 5.0)
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

        post = await repo.complete_task_if(
            NotBlankStr("ct-6"), NotBlankStr("task-a"), 5.0
        )

        assert post is not None
        assert post.completed_task_ids == ("task-a",)

    async def test_returns_none_for_a_missing_row(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert (
            await repo.complete_task_if(
                NotBlankStr("no-such-sprint"), NotBlankStr("task-a"), 5.0
            )
            is None
        )


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
