"""Conformance tests for ``PlanRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid
from tests.unit.persistence.conftest import make_task

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: The objective task every plan here decomposes. ``plans.parent_task_id`` is
#: a foreign key, so the parent has to exist before any plan referencing it.
_PARENT_TASK_ID = "task-root"

#: Both backends answer a foreign-key refusal with this SQLSTATE, which is
#: the whole point of mapping SQLite's message onto the standard codes.
_SQLSTATE_FOREIGN_KEY = "23503"

#: Wire status values the guarded delete reads as finished, spelled out here
#: rather than imported so a change to the production set has to be made
#: deliberately in both places.
_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "rejected"})


@pytest.fixture(autouse=True)
async def _parent_task(backend: PersistenceBackend) -> None:
    """Persist the objective task the plans in this module point at."""
    await backend.tasks.save(make_task(task_id=_PARENT_TASK_ID, title="Ship the game"))


def _plan(
    *,
    plan_id: str = "plan-001",
    project: str = "beachhead",
    objective_id: str = "obj-001",
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
) -> Plan:
    return Plan(
        id=as_uuid(plan_id),
        project=NotBlankStr(project),
        objective_id=NotBlankStr(objective_id),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(sid(_PARENT_TASK_ID)),
        items=(
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Scaffold board"),
                description=NotBlankStr("Set up the game board grid"),
                estimated_complexity=Complexity.MEDIUM,
                acceptance_criteria=(NotBlankStr("board grid renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
                satisfies=(NotBlankStr("A playable board"),),
            ),
            PlanItem(
                id=NotBlankStr(sid("item-2")),
                title=NotBlankStr("Piece movement"),
                description=NotBlankStr("Implement piece drop + rotation"),
                dependencies=(NotBlankStr(sid("item-1")),),
                owner=NotBlankStr("engineering"),
                acceptance_criteria=(NotBlankStr("pieces drop and rotate"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        coordination_topology=CoordinationTopology.AUTO,
        status=status,
        objective_criteria=(NotBlankStr("A playable board"), NotBlankStr("Scoring")),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestPlanRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(_plan())

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.id == as_uuid("plan-001")
        assert fetched.objective_id == "obj-001"
        assert fetched.status is PlanStatus.PENDING_REVIEW
        assert len(fetched.items) == 2
        assert fetched.items[1].dependencies == (sid("item-1"),)
        assert fetched.items[1].owner == "engineering"
        assert fetched.items[0].satisfies == ("A playable board",)
        assert fetched.objective_criteria == ("A playable board", "Scoring")
        assert fetched.created_at == _CREATED_AT

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.plans.get(NotBlankStr("ghost")) is None

    @pytest.mark.parametrize(
        "status",
        [PlanStatus.INTEGRATING, PlanStatus.EVALUATING],
        ids=lambda value: str(value.value),
    )
    async def test_a_tail_status_round_trips(
        self, backend: PersistenceBackend, status: PlanStatus
    ) -> None:
        """Both backends CHECK the status literal, so the enum is not enough.

        A typo in either migration's constraint would only surface the first
        time an initiative reached the tail against a real database.
        """
        await backend.plans.save(_plan(status=status))

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.status is status

    async def test_the_replan_generation_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        """The cap that stops a runaway replan chain reads this column."""
        await backend.plans.save(
            _plan().model_copy(update={"replan_generation": 3}),
        )

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.replan_generation == 3

    async def test_replan_generation_defaults_to_zero(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.plans.save(_plan())

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.replan_generation == 0

    async def test_planning_provenance_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        """The two fields the approval gate reads are the operator's warning.

        ``planning_strategy`` says which planner produced the items and
        ``review_absent_reason`` says why a seated panel returned nothing, so a
        column missing from the write list turns both into a silent ``None``
        and the operator approves a plan carrying no quality signal without
        being told.
        """
        await backend.plans.save(
            _plan().model_copy(
                update={
                    "planning_strategy": "single_shot_fallback",
                    "review_absent_reason": "panel produced no verdicts",
                }
            ),
        )

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.planning_strategy == "single_shot_fallback"
        assert fetched.review_absent_reason == "panel produced no verdicts"

    async def test_planning_provenance_round_trips_through_update(
        self, backend: PersistenceBackend
    ) -> None:
        """``update`` writes its own column list, so it is a separate risk."""
        original = _plan(plan_id="p-prov")
        await backend.plans.create(original)

        await backend.plans.update(
            original.model_copy(
                update={
                    "planning_strategy": "researched",
                    "review_absent_reason": "no panel seated",
                }
            ),
        )

        fetched = await backend.plans.get(NotBlankStr(sid("p-prov")))
        assert fetched is not None
        assert fetched.planning_strategy == "researched"
        assert fetched.review_absent_reason == "no panel seated"

    async def test_planning_provenance_defaults_to_absent(
        self, backend: PersistenceBackend
    ) -> None:
        """The common case says nothing: configured planner, real review."""
        await backend.plans.save(_plan())

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.planning_strategy is None
        assert fetched.review_absent_reason is None

    async def test_save_upsert(self, backend: PersistenceBackend) -> None:
        plan = _plan()
        await backend.plans.save(plan)

        approved = plan.model_copy(update={"status": PlanStatus.APPROVED, "version": 2})
        await backend.plans.save(approved)

        fetched = await backend.plans.get(NotBlankStr(sid("plan-001")))
        assert fetched is not None
        assert fetched.status is PlanStatus.APPROVED
        assert fetched.version == 2

    async def test_list_items_in_id_order(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(_plan(plan_id="p1"))
        await backend.plans.save(_plan(plan_id="p2"))

        rows = await backend.plans.list_items()
        expected = sorted([str(as_uuid("p1")), str(as_uuid("p2"))])
        ids = [str(r.id) for r in rows if str(r.id) in expected]
        assert ids == expected

    async def test_query_filter_by_status(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(
            _plan(plan_id="approved", status=PlanStatus.APPROVED),
        )
        await backend.plans.save(
            _plan(plan_id="pending", status=PlanStatus.PENDING_REVIEW),
        )

        rows = await backend.plans.query(
            PlanFilterSpec(status=PlanStatus.APPROVED),
        )
        ids = {r.id for r in rows}
        assert as_uuid("approved") in ids
        assert as_uuid("pending") not in ids

    async def test_query_filter_by_objective(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(_plan(plan_id="alpha", objective_id="obj-a"))
        await backend.plans.save(_plan(plan_id="beta", objective_id="obj-b"))

        rows = await backend.plans.query(
            PlanFilterSpec(objective_id=NotBlankStr("obj-a")),
        )
        assert [r.id for r in rows] == [as_uuid("alpha")]

    async def test_query_filter_by_project(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(_plan(plan_id="x", project="proj-x"))
        await backend.plans.save(_plan(plan_id="y", project="proj-y"))

        rows = await backend.plans.query(
            PlanFilterSpec(project=NotBlankStr("proj-x")),
        )
        assert [r.id for r in rows] == [as_uuid("x")]

    async def test_count_matches_filter(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(
            _plan(plan_id="c1", status=PlanStatus.APPROVED),
        )
        await backend.plans.save(
            _plan(plan_id="c2", status=PlanStatus.APPROVED),
        )
        await backend.plans.save(
            _plan(plan_id="c3", status=PlanStatus.REJECTED),
        )

        assert (
            await backend.plans.count(PlanFilterSpec(status=PlanStatus.APPROVED)) == 2
        )

    async def test_query_respects_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.plans.save(_plan(plan_id=f"p-{i:02d}"))

        rows = await backend.plans.query(PlanFilterSpec(), limit=3)
        assert len(rows) == 3

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.plans.save(_plan())

        deleted = await backend.plans.delete(NotBlankStr(sid("plan-001")))
        assert deleted is True
        assert await backend.plans.get(NotBlankStr(sid("plan-001"))) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.plans.delete(NotBlankStr("ghost")) is False

    async def test_the_guarded_delete_removes_a_plan_with_no_tasks(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.plans.save(_plan())

        outcome = await backend.plans.delete_if_no_live_tasks(
            NotBlankStr(sid("plan-001")), terminal_statuses=_TERMINAL_STATUSES
        )

        assert outcome.deleted is True
        assert outcome.live_task_count == 0
        assert await backend.plans.get(NotBlankStr(sid("plan-001"))) is None

    async def test_the_guarded_delete_refuses_a_plan_with_live_work(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting it would strand the task on a plan id that resolves to nothing."""
        plan = _plan()
        await backend.plans.save(plan)
        await backend.tasks.save(
            make_task(
                task_id="task-live",
                plan_id=plan.id,
                status=TaskStatus.IN_PROGRESS,
            )
        )

        outcome = await backend.plans.delete_if_no_live_tasks(
            NotBlankStr(sid("plan-001")), terminal_statuses=_TERMINAL_STATUSES
        )

        assert outcome.deleted is False
        assert outcome.live_task_count == 1
        assert await backend.plans.get(NotBlankStr(sid("plan-001"))) is not None

    async def test_the_guarded_delete_ignores_finished_work(
        self, backend: PersistenceBackend
    ) -> None:
        """A completed task is not building, so it cannot hold the plan open."""
        plan = _plan()
        await backend.plans.save(plan)
        await backend.tasks.save(
            make_task(
                task_id="task-done",
                plan_id=plan.id,
                status=TaskStatus.COMPLETED,
            )
        )

        outcome = await backend.plans.delete_if_no_live_tasks(
            NotBlankStr(sid("plan-001")), terminal_statuses=_TERMINAL_STATUSES
        )

        assert outcome.deleted is True
        assert outcome.live_task_count == 0

    async def test_the_guarded_delete_reports_a_missing_plan(
        self, backend: PersistenceBackend
    ) -> None:
        """Nothing deleted and nothing blocking says the row was never there."""
        outcome = await backend.plans.delete_if_no_live_tasks(
            NotBlankStr(sid("ghost")), terminal_statuses=_TERMINAL_STATUSES
        )

        assert outcome.deleted is False
        assert outcome.live_task_count == 0

    async def test_no_declared_terminal_status_holds_every_task_live(
        self, backend: PersistenceBackend
    ) -> None:
        """Nothing declared finished means nothing may be assumed finished."""
        plan = _plan()
        await backend.plans.save(plan)
        await backend.tasks.save(
            make_task(
                task_id="task-done",
                plan_id=plan.id,
                status=TaskStatus.COMPLETED,
            )
        )

        outcome = await backend.plans.delete_if_no_live_tasks(
            NotBlankStr(sid("plan-001")), terminal_statuses=frozenset()
        )

        assert outcome.deleted is False
        assert outcome.live_task_count == 1

    async def test_a_plan_cannot_name_a_task_that_does_not_exist(
        self, backend: PersistenceBackend
    ) -> None:
        """The parent reference is enforced, not merely non-blank.

        Without this a deleted (or never-created) task left the plan
        pointing at nothing, and the orphan still ran to completion and
        reached the operator's review queue.
        """
        orphan = _plan(plan_id="p-orphan").model_copy(
            update={"parent_task_id": NotBlankStr(sid("task-never-created"))}
        )

        # ConstraintViolationError, not the QueryError base: a bare
        # ``QueryError`` is satisfied by any repository failure, so it
        # would pass with the foreign key removed. It is also retryable
        # and 500, where this is permanent and a 4xx.
        with pytest.raises(ConstraintViolationError) as info:
            await backend.plans.save(orphan)
        assert info.value.sqlstate == _SQLSTATE_FOREIGN_KEY
        assert info.value.is_retryable is False

    async def test_deleting_a_task_a_plan_owns_is_refused(
        self, backend: PersistenceBackend
    ) -> None:
        """RESTRICT: the plan is a decision record, not task-delete debris."""
        await backend.plans.save(_plan(plan_id="p-holds"))

        with pytest.raises(ConstraintViolationError) as info:
            await backend.tasks.delete(sid(_PARENT_TASK_ID))
        assert info.value.sqlstate == _SQLSTATE_FOREIGN_KEY
        assert info.value.is_retryable is False

    async def test_the_task_deletes_once_its_plan_is_gone(
        self, backend: PersistenceBackend
    ) -> None:
        """The refusal is a gate, not a trap: resolving the plan clears it."""
        await backend.plans.save(_plan(plan_id="p-transient"))

        await backend.plans.delete(NotBlankStr(sid("p-transient")))

        assert await backend.tasks.delete(sid(_PARENT_TASK_ID)) is True

    async def test_query_filter_by_parent_task(
        self, backend: PersistenceBackend
    ) -> None:
        """The task-delete guard reads plans by their parent, so it is indexed."""
        await backend.plans.save(_plan(plan_id="p-parented"))

        rows = await backend.plans.query(
            PlanFilterSpec(parent_task_id=NotBlankStr(sid(_PARENT_TASK_ID)))
        )
        assert {str(row.id) for row in rows} == {sid("p-parented")}

        assert (
            await backend.plans.query(
                PlanFilterSpec(parent_task_id=NotBlankStr(sid("other-task")))
            )
            == ()
        )

    async def test_create_inserts_new_row(self, backend: PersistenceBackend) -> None:
        await backend.plans.create(_plan(plan_id="p-create"))

        fetched = await backend.plans.get(NotBlankStr(sid("p-create")))
        assert fetched is not None
        assert fetched.id == as_uuid("p-create")

    async def test_create_rejects_duplicate(self, backend: PersistenceBackend) -> None:
        await backend.plans.create(_plan(plan_id="p-dup"))

        with pytest.raises(DuplicateRecordError):
            await backend.plans.create(_plan(plan_id="p-dup"))

    async def test_update_modifies_existing_row(
        self, backend: PersistenceBackend
    ) -> None:
        original = _plan(plan_id="p-up")
        await backend.plans.create(original)

        approved = original.model_copy(update={"status": PlanStatus.APPROVED})
        await backend.plans.update(approved)

        fetched = await backend.plans.get(NotBlankStr(sid("p-up")))
        assert fetched is not None
        assert fetched.status is PlanStatus.APPROVED

    async def test_update_rejects_missing(self, backend: PersistenceBackend) -> None:
        with pytest.raises(RecordNotFoundError):
            await backend.plans.update(_plan(plan_id="p-ghost"))

    async def test_update_version_guard_rejects_stale_writer(
        self, backend: PersistenceBackend
    ) -> None:
        original = _plan(plan_id="p-ver")  # version 1
        await backend.plans.create(original)

        # A first writer, holding version 1, bumps the row to version 2.
        await backend.plans.update(
            original.model_copy(update={"version": 2}), expected_version=1
        )

        # A second writer still holding the stale version 1 is rejected rather
        # than silently clobbering the first writer's edit.
        with pytest.raises(PersistenceVersionConflictError):
            await backend.plans.update(
                original.model_copy(
                    update={"status": PlanStatus.APPROVED, "version": 2}
                ),
                expected_version=1,
            )

    async def test_update_version_guard_missing_row_is_not_found(
        self, backend: PersistenceBackend
    ) -> None:
        # A version-guarded update on a row that does not exist at all is a
        # not-found, not a version conflict.
        with pytest.raises(RecordNotFoundError):
            await backend.plans.update(_plan(plan_id="p-absent"), expected_version=1)

    async def test_list_items_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.plans.list_items() == ()

    @pytest.mark.parametrize(
        ("limit", "offset"),
        [(0, 0), (-1, 0), (1, -1)],
    )
    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend, limit: int, offset: int
    ) -> None:
        with pytest.raises(QueryError):
            await backend.plans.list_items(limit=limit, offset=offset)

    @pytest.mark.parametrize(
        ("limit", "offset"),
        [(0, 0), (-1, 0), (1, -1)],
    )
    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend, limit: int, offset: int
    ) -> None:
        with pytest.raises(QueryError):
            await backend.plans.query(PlanFilterSpec(), limit=limit, offset=offset)
