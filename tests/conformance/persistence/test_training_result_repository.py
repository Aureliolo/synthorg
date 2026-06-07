"""Conformance tests for ``TrainingResultRepository``."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingResult,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_START = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
_COMPLETE = _START + timedelta(minutes=5)


async def _seed_plan(
    backend: PersistenceBackend,
    plan_id: str,
    agent_id: str = "agent-new-001",
) -> None:
    """Satisfy the training_results -> training_plans FK."""
    await backend.training_plans.save(
        TrainingPlan(
            id=NotBlankStr(plan_id),
            new_agent_id=NotBlankStr(agent_id),
            new_agent_role=NotBlankStr("engineer"),
            new_agent_level=SeniorityLevel.JUNIOR,
            created_at=_START,
        ),
    )


def _result(
    *,
    result_id: str = "res-001",
    plan_id: str = "plan-001",
    agent_id: str = "agent-new-001",
    completed_at: datetime = _COMPLETE,
) -> TrainingResult:
    return TrainingResult(
        id=NotBlankStr(result_id),
        plan_id=NotBlankStr(plan_id),
        new_agent_id=NotBlankStr(agent_id),
        source_agents_used=(NotBlankStr("agent-senior-1"),),
        items_extracted=((ContentType.PROCEDURAL, 7),),
        items_after_curation=((ContentType.PROCEDURAL, 5),),
        items_after_guards=((ContentType.PROCEDURAL, 4),),
        items_stored=((ContentType.PROCEDURAL, 4),),
        started_at=_START,
        completed_at=completed_at,
    )


class TestTrainingResultRepository:
    async def test_save_and_get_by_plan(self, backend: PersistenceBackend) -> None:
        await _seed_plan(backend, "plan-001")
        await backend.training_results.save(_result())

        fetched = await backend.training_results.get_by_plan(
            NotBlankStr("plan-001"),
        )
        assert fetched is not None
        assert fetched.new_agent_id == "agent-new-001"

    async def test_get_by_plan_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.training_results.get_by_plan(NotBlankStr("ghost")) is None

    async def test_save_upserts_on_id(self, backend: PersistenceBackend) -> None:
        await _seed_plan(backend, "plan-001")
        await backend.training_results.save(_result())
        await backend.training_results.save(
            _result(completed_at=_COMPLETE + timedelta(minutes=1)),
        )

        fetched = await backend.training_results.get_by_plan(
            NotBlankStr("plan-001"),
        )
        assert fetched is not None
        assert fetched.completed_at == _COMPLETE + timedelta(minutes=1)

    async def test_get_latest_returns_most_recent(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_plan(backend, "p-old")
        await _seed_plan(backend, "p-new")
        await backend.training_results.save(
            _result(result_id="old", plan_id="p-old", completed_at=_COMPLETE),
        )
        await backend.training_results.save(
            _result(
                result_id="new",
                plan_id="p-new",
                completed_at=_COMPLETE + timedelta(minutes=1),
            ),
        )

        latest = await backend.training_results.get_latest(
            NotBlankStr("agent-new-001"),
        )
        assert latest is not None
        assert latest.id == "new"

    async def test_get_latest_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.training_results.get_latest(NotBlankStr("ghost")) is None

    async def test_get_by_id(self, backend: PersistenceBackend) -> None:
        await _seed_plan(backend, "plan-001")
        await backend.training_results.save(_result())

        fetched = await backend.training_results.get(NotBlankStr("res-001"))
        assert fetched is not None
        assert fetched.new_agent_id == "agent-new-001"

    async def test_get_by_id_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.training_results.get(NotBlankStr("ghost")) is None

    async def test_delete(self, backend: PersistenceBackend) -> None:
        await _seed_plan(backend, "plan-001")
        await backend.training_results.save(_result())

        deleted = await backend.training_results.delete(NotBlankStr("res-001"))
        assert deleted is True

        fetched = await backend.training_results.get(NotBlankStr("res-001"))
        assert fetched is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.training_results.delete(NotBlankStr("ghost"))
        assert deleted is False

    async def test_list_items_ordered_by_id(self, backend: PersistenceBackend) -> None:
        for plan_id in ("plan-c", "plan-a", "plan-b"):
            await _seed_plan(backend, plan_id)
        await backend.training_results.save(
            _result(result_id="c-res", plan_id="plan-c")
        )
        await backend.training_results.save(
            _result(result_id="a-res", plan_id="plan-a")
        )
        await backend.training_results.save(
            _result(result_id="b-res", plan_id="plan-b")
        )

        rows = await backend.training_results.list_items()
        ids = [r.id for r in rows]
        assert ids == ["a-res", "b-res", "c-res"]

    async def test_list_items_with_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(5):
            await _seed_plan(backend, f"plan-{i:02d}")
            await backend.training_results.save(
                _result(result_id=f"res-{i:02d}", plan_id=f"plan-{i:02d}"),
            )

        page1 = await backend.training_results.list_items(limit=2, offset=0)
        page2 = await backend.training_results.list_items(limit=2, offset=2)
        page3 = await backend.training_results.list_items(limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
