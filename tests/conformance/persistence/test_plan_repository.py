"""Conformance tests for ``PlanRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import Complexity, CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


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
        parent_task_id=NotBlankStr("task-root"),
        items=(
            PlanItem(
                id=NotBlankStr("i1"),
                title=NotBlankStr("Scaffold board"),
                description=NotBlankStr("Set up the game board grid"),
                estimated_complexity=Complexity.MEDIUM,
            ),
            PlanItem(
                id=NotBlankStr("i2"),
                title=NotBlankStr("Piece movement"),
                description=NotBlankStr("Implement piece drop + rotation"),
                dependencies=(NotBlankStr("i1"),),
                owner=NotBlankStr("engineering"),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        coordination_topology=CoordinationTopology.AUTO,
        status=status,
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
        assert fetched.items[1].dependencies == ("i1",)
        assert fetched.items[1].owner == "engineering"
        assert fetched.created_at == _CREATED_AT

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.plans.get(NotBlankStr("ghost")) is None

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
