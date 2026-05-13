"""Tests for SQLiteTrainingPlanRepository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.training.models import ContentType, TrainingPlan, TrainingPlanStatus
from synthorg.persistence.sqlite.training_plan_repo import (
    SQLiteTrainingPlanRepository,
)
from tests._shared.persistence import make_private_write_context


def _make_plan(  # noqa: PLR0913
    *,
    plan_id: str = "plan-001",
    agent_id: str = "agent-new-001",
    agent_role: str = "engineer",
    agent_level: SeniorityLevel = SeniorityLevel.JUNIOR,
    department: str | None = "engineering",
    status: TrainingPlanStatus = TrainingPlanStatus.PENDING,
    skip_training: bool = False,
    created_at: datetime = datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    executed_at: datetime | None = None,
) -> TrainingPlan:
    return TrainingPlan(
        id=plan_id,
        new_agent_id=agent_id,
        new_agent_role=agent_role,
        new_agent_level=agent_level,
        new_agent_department=department,
        enabled_content_types=frozenset(ContentType),
        volume_caps=(
            (ContentType.PROCEDURAL, 50),
            (ContentType.SEMANTIC, 10),
            (ContentType.TOOL_PATTERNS, 20),
        ),
        skip_training=skip_training,
        require_review=True,
        status=status,
        created_at=created_at,
        executed_at=executed_at,
    )


@pytest.fixture
def repo(migrated_db: aiosqlite.Connection) -> SQLiteTrainingPlanRepository:
    return SQLiteTrainingPlanRepository(
        migrated_db, write_context=make_private_write_context()
    )


@pytest.mark.unit
class TestSQLiteTrainingPlanRepository:
    async def test_save_and_get(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plan = _make_plan()
        await repo.save(plan)
        fetched = await repo.get("plan-001")
        assert fetched is not None
        assert fetched.id == "plan-001"
        assert fetched.new_agent_id == "agent-new-001"
        assert fetched.new_agent_role == "engineer"
        assert fetched.new_agent_level is SeniorityLevel.JUNIOR
        assert fetched.new_agent_department == "engineering"
        assert fetched.status is TrainingPlanStatus.PENDING
        assert fetched.skip_training is False
        assert fetched.require_review is True
        assert fetched.enabled_content_types == frozenset(ContentType)
        assert len(fetched.volume_caps) == 3

    async def test_get_returns_none_for_missing(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        result = await repo.get("nonexistent")
        assert result is None

    async def test_save_upsert_updates_existing(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plan = _make_plan()
        await repo.save(plan)
        updated = plan.model_copy(
            update={
                "status": TrainingPlanStatus.EXECUTED,
                "executed_at": datetime(2026, 4, 1, 13, 0, tzinfo=UTC),
            }
        )
        await repo.save(updated)
        fetched = await repo.get("plan-001")
        assert fetched is not None
        assert fetched.status is TrainingPlanStatus.EXECUTED
        assert fetched.executed_at is not None

    async def test_latest_pending(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        old_plan = _make_plan(
            plan_id="plan-old",
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        new_plan = _make_plan(
            plan_id="plan-new",
            created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        executed = _make_plan(
            plan_id="plan-exec",
            status=TrainingPlanStatus.EXECUTED,
            created_at=datetime(2026, 4, 2, tzinfo=UTC),
            executed_at=datetime(2026, 4, 2, 1, tzinfo=UTC),
        )
        await repo.save(old_plan)
        await repo.save(new_plan)
        await repo.save(executed)

        latest = await repo.latest_pending("agent-new-001")
        assert latest is not None
        assert latest.id == "plan-new"

    async def test_latest_pending_returns_none(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        result = await repo.latest_pending("nonexistent")
        assert result is None

    async def test_list_by_agent(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plan_a = _make_plan(
            plan_id="plan-a",
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        plan_b = _make_plan(
            plan_id="plan-b",
            created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        other = _make_plan(
            plan_id="plan-other",
            agent_id="agent-other",
        )
        await repo.save(plan_a)
        await repo.save(plan_b)
        await repo.save(other)

        plans = await repo.list_by_agent("agent-new-001")
        assert len(plans) == 2
        assert plans[0].id == "plan-b"
        assert plans[1].id == "plan-a"

    async def test_list_by_agent_empty(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plans = await repo.list_by_agent("nonexistent")
        assert plans == ()

    async def test_nullable_department(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plan = _make_plan(department=None)
        await repo.save(plan)
        fetched = await repo.get("plan-001")
        assert fetched is not None
        assert fetched.new_agent_department is None

    async def test_override_sources_roundtrip(
        self,
        repo: SQLiteTrainingPlanRepository,
    ) -> None:
        plan = _make_plan()
        plan = plan.model_copy(
            update={
                "override_sources": ("senior-1", "senior-2"),
            }
        )
        await repo.save(plan)
        fetched = await repo.get("plan-001")
        assert fetched is not None
        assert fetched.override_sources == ("senior-1", "senior-2")
