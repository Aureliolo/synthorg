"""Integration test: training plan -> execute -> persist -> fetch.

Exercises the full training persistence pipeline end-to-end using
an in-memory SQLite backend and the real TrainingService with
mocked extractors and guards.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
    TrainingPlanStatus,
)
from synthorg.hr.training.protocol import CurationStrategy, SourceSelector
from synthorg.hr.training.service import TrainingService
from synthorg.memory.protocol import MemoryBackend
from synthorg.persistence.sqlite.training_plan_repo import (
    SQLiteTrainingPlanRepository,
)
from synthorg.persistence.sqlite.training_result_repo import (
    SQLiteTrainingResultRepository,
)
from tests._shared import mock_of
from tests._shared.persistence import make_private_write_context


def _make_item(
    *,
    source: str = "senior-1",
    ct: ContentType = ContentType.PROCEDURAL,
    content: str = "How to deploy",
) -> TrainingItem:
    return TrainingItem(
        source_agent_id=source,
        content_type=ct,
        content=content,
        relevance_score=0.8,
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
    )


@pytest.mark.integration
class TestTrainingPersistencePipeline:
    async def test_full_hire_flow(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """Plan -> execute -> persist result -> fetch result."""
        write_context = make_private_write_context()
        plan_repo = SQLiteTrainingPlanRepository(
            migrated_db, write_context=write_context
        )
        result_repo = SQLiteTrainingResultRepository(
            migrated_db, write_context=write_context
        )

        # 1. Create and persist a training plan.
        plan = TrainingPlan(
            new_agent_id="new-hire-001",
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            new_agent_department="engineering",
            enabled_content_types=frozenset({ContentType.PROCEDURAL}),
            volume_caps=((ContentType.PROCEDURAL, 50),),
            skip_training=False,
            require_review=False,
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        await plan_repo.save(plan)

        # Verify plan is persisted.
        fetched_plan = await plan_repo.get(plan.id)
        assert fetched_plan is not None
        assert fetched_plan.status is TrainingPlanStatus.PENDING

        # 2. Build TrainingService with mocked components.
        items = (_make_item(),)
        mock_selector = mock_of[SourceSelector]()
        mock_selector.select.return_value = (NotBlankStr("senior-1"),)
        mock_extractor = AsyncMock()
        mock_extractor.content_type = ContentType.PROCEDURAL
        mock_extractor.extract.return_value = items

        mock_curation = mock_of[CurationStrategy]()
        mock_curation.curate.return_value = items

        # Pass-through guard that approves everything.
        mock_guard = AsyncMock()
        mock_guard.name = "pass_through"
        mock_guard.evaluate.return_value = TrainingGuardDecision(
            approved_items=items,
            rejected_count=0,
            guard_name="pass_through",
        )

        mock_memory = mock_of[MemoryBackend]()

        service = TrainingService(
            selector=mock_selector,
            extractors={ContentType.PROCEDURAL: mock_extractor},
            curation=mock_curation,
            guards=(mock_guard,),
            memory_backend=mock_memory,
        )

        # 3. Execute the plan.
        result = await service.execute(plan)
        assert result.plan_id == plan.id
        assert result.new_agent_id == "new-hire-001"
        assert len(result.source_agents_used) == 1

        # 4. Persist the result and transition plan status.
        executed_plan = plan.model_copy(
            update={
                "status": TrainingPlanStatus.EXECUTED,
                "executed_at": result.completed_at,
            }
        )
        await plan_repo.save(executed_plan)
        await result_repo.save(result)

        # 5. Fetch and verify.
        stored_plan = await plan_repo.get(plan.id)
        assert stored_plan is not None
        assert stored_plan.status is TrainingPlanStatus.EXECUTED
        assert stored_plan.executed_at is not None

        stored_result = await result_repo.get_latest("new-hire-001")
        assert stored_result is not None
        assert stored_result.id == result.id
        assert stored_result.plan_id == plan.id

        # Also verify get_by_plan.
        by_plan = await result_repo.get_by_plan(plan.id)
        assert by_plan is not None
        assert by_plan.id == result.id
