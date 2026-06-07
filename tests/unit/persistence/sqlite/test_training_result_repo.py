"""Tests for SQLiteTrainingResultRepository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingApprovalHandle,
    TrainingPlan,
    TrainingResult,
)
from synthorg.persistence.sqlite.training_plan_repo import (
    SQLiteTrainingPlanRepository,
)
from synthorg.persistence.sqlite.training_result_repo import (
    SQLiteTrainingResultRepository,
)
from tests._shared.persistence import make_private_write_context


def _make_plan(
    *,
    plan_id: str = "plan-001",
    agent_id: str = "agent-new-001",
) -> TrainingPlan:
    return TrainingPlan(
        id=plan_id,
        new_agent_id=agent_id,
        new_agent_role="engineer",
        new_agent_level=SeniorityLevel.JUNIOR,
        new_agent_department="engineering",
        enabled_content_types=frozenset(ContentType),
        skip_training=False,
        require_review=True,
        created_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )


def _make_result(  # noqa: PLR0913
    *,
    result_id: str = "result-001",
    plan_id: str = "plan-001",
    agent_id: str = "agent-new-001",
    started_at: datetime = datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    completed_at: datetime = datetime(2026, 4, 1, 12, 5, tzinfo=UTC),
    review_pending: bool = False,
) -> TrainingResult:
    return TrainingResult(
        id=result_id,
        plan_id=plan_id,
        new_agent_id=agent_id,
        source_agents_used=("senior-1", "senior-2"),
        items_extracted=(
            (ContentType.PROCEDURAL, 10),
            (ContentType.SEMANTIC, 5),
        ),
        items_after_curation=(
            (ContentType.PROCEDURAL, 8),
            (ContentType.SEMANTIC, 4),
        ),
        items_after_guards=(
            (ContentType.PROCEDURAL, 7),
            (ContentType.SEMANTIC, 3),
        ),
        items_stored=(
            (ContentType.PROCEDURAL, 7),
            (ContentType.SEMANTIC, 3),
        ),
        review_pending=review_pending,
        errors=("Sanitization: item too long",),
        started_at=started_at,
        completed_at=completed_at,
    )


@pytest.fixture
async def repos(
    migrated_db: aiosqlite.Connection,
) -> tuple[SQLiteTrainingPlanRepository, SQLiteTrainingResultRepository]:
    write_context = make_private_write_context()
    plan_repo = SQLiteTrainingPlanRepository(migrated_db, write_context=write_context)
    result_repo = SQLiteTrainingResultRepository(
        migrated_db, write_context=write_context
    )
    return plan_repo, result_repo


@pytest.mark.unit
class TestSQLiteTrainingResultRepository:
    async def test_save_and_get_by_plan(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        plan_repo, result_repo = repos
        await plan_repo.save(_make_plan())
        result = _make_result()
        await result_repo.save(result)

        fetched = await result_repo.get_by_plan("plan-001")
        assert fetched is not None
        assert fetched.id == "result-001"
        assert fetched.plan_id == "plan-001"
        assert fetched.new_agent_id == "agent-new-001"
        assert len(fetched.source_agents_used) == 2
        assert len(fetched.items_extracted) == 2
        assert fetched.errors == ("Sanitization: item too long",)

    async def test_get_by_plan_returns_none(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        _, result_repo = repos
        result = await result_repo.get_by_plan("nonexistent")
        assert result is None

    async def test_get_latest(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        plan_repo, result_repo = repos
        await plan_repo.save(_make_plan(plan_id="plan-old"))
        await plan_repo.save(_make_plan(plan_id="plan-new"))

        old_result = _make_result(
            result_id="result-old",
            plan_id="plan-old",
            started_at=datetime(2026, 3, 1, tzinfo=UTC),
            completed_at=datetime(2026, 3, 1, 0, 5, tzinfo=UTC),
        )
        new_result = _make_result(
            result_id="result-new",
            plan_id="plan-new",
            started_at=datetime(2026, 4, 1, tzinfo=UTC),
            completed_at=datetime(2026, 4, 1, 0, 5, tzinfo=UTC),
        )
        await result_repo.save(old_result)
        await result_repo.save(new_result)

        latest = await result_repo.get_latest("agent-new-001")
        assert latest is not None
        assert latest.id == "result-new"

    async def test_get_latest_returns_none(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        _, result_repo = repos
        result = await result_repo.get_latest("nonexistent")
        assert result is None

    async def test_pending_approvals_roundtrip(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        plan_repo, result_repo = repos
        await plan_repo.save(_make_plan())

        result = _make_result(review_pending=True)
        result = result.model_copy(
            update={
                "approval_item_id": "approval-1",
                "pending_approvals": (
                    TrainingApprovalHandle(
                        approval_item_id="approval-1",
                        content_type=ContentType.PROCEDURAL,
                        item_count=3,
                    ),
                ),
            }
        )
        await result_repo.save(result)

        fetched = await result_repo.get_by_plan("plan-001")
        assert fetched is not None
        assert fetched.review_pending is True
        assert fetched.approval_item_id == "approval-1"
        assert len(fetched.pending_approvals) == 1
        handle = fetched.pending_approvals[0]
        assert handle.approval_item_id == "approval-1"
        assert handle.content_type is ContentType.PROCEDURAL
        assert handle.item_count == 3

    async def test_upsert_updates_existing(
        self,
        repos: tuple[
            SQLiteTrainingPlanRepository,
            SQLiteTrainingResultRepository,
        ],
    ) -> None:
        plan_repo, result_repo = repos
        await plan_repo.save(_make_plan())

        result = _make_result()
        await result_repo.save(result)

        updated = result.model_copy(
            update={
                "review_pending": True,
            }
        )
        await result_repo.save(updated)

        fetched = await result_repo.get_by_plan("plan-001")
        assert fetched is not None
        assert fetched.review_pending is True
