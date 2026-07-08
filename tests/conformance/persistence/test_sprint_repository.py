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
        await repo.save(_make_sprint(sprint_id="q1", sprint_number=1))
        await repo.save(
            _make_sprint(
                sprint_id="q2",
                sprint_number=2,
                status=SprintStatus.ACTIVE,
                start_date=_START,
            )
        )

        planning = await repo.query(SprintFilterSpec(status=SprintStatus.PLANNING))
        assert {s.id for s in planning} >= {"q1"}
        assert "q2" not in {s.id for s in planning}

    async def test_count_matches_query(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="c1", project="gamma", sprint_number=1))
        await repo.save(_make_sprint(sprint_id="c2", project="gamma", sprint_number=2))
        await repo.save(_make_sprint(sprint_id="c3", project="delta", sprint_number=1))

        spec = SprintFilterSpec(project="gamma")
        assert await repo.count(spec) == len(await repo.query(spec))
        assert await repo.count(spec) == 2

    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="l1", project="p", sprint_number=1))
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
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="u1", project="proj-u", sprint_number=1))
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_sprint(sprint_id="u2", project="proj-u", sprint_number=1)
            )

    async def test_unique_org_wide_sprint_number(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_sprint(sprint_id="ow1", project=None, sprint_number=1))
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
