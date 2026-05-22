"""Stakes propagation from decomposition into created tasks."""

import pytest

from synthorg.core.enums import Complexity, Stakes, TaskType
from synthorg.core.task import Task
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService


class _StaticStrategy:
    """Decomposition strategy returning a fixed plan (no LLM)."""

    def __init__(self, plan: DecompositionPlan) -> None:
        self._plan = plan

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        del task, context
        return self._plan

    def get_strategy_name(self) -> str:
        return "static-test"


def _parent_task() -> Task:
    return Task(
        id="parent-1",
        title="Parent",
        description="Parent task",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
    )


@pytest.mark.unit
class TestDecompositionStakesPropagation:
    """The service assesses each subtask and stamps stakes on its task."""

    async def test_mixed_subtasks_get_distinct_stakes(self) -> None:
        plan = DecompositionPlan(
            parent_task_id="parent-1",
            subtasks=(
                SubtaskDefinition(
                    id="low",
                    title="Update changelog",
                    description="Tidy the docs wording",
                    estimated_complexity=Complexity.SIMPLE,
                ),
                SubtaskDefinition(
                    id="high",
                    title="Design the architecture",
                    description="Make the core architecture decision",
                    estimated_complexity=Complexity.COMPLEX,
                ),
                SubtaskDefinition(
                    id="critical",
                    title="Production migration",
                    description="Run an irreversible production deployment",
                    estimated_complexity=Complexity.MEDIUM,
                ),
            ),
        )
        service = DecompositionService(_StaticStrategy(plan), TaskStructureClassifier())

        result = await service.decompose_task(
            _parent_task(),
            DecompositionContext(),
        )

        stakes_by_id = {t.id: t.stakes for t in result.created_tasks}
        assert stakes_by_id["low"] is Stakes.LOW
        assert stakes_by_id["high"] is Stakes.HIGH
        assert stakes_by_id["critical"] is Stakes.CRITICAL

    async def test_plan_subtasks_carry_same_stakes_as_tasks(self) -> None:
        plan = DecompositionPlan(
            parent_task_id="parent-1",
            subtasks=(
                SubtaskDefinition(
                    id="only",
                    title="Refactor the payment flow",
                    description="Touch the billing path",
                    estimated_complexity=Complexity.SIMPLE,
                ),
            ),
        )
        service = DecompositionService(_StaticStrategy(plan), TaskStructureClassifier())

        result = await service.decompose_task(
            _parent_task(),
            DecompositionContext(),
        )

        plan_stakes = {s.id: s.stakes for s in result.plan.subtasks}
        task_stakes = {t.id: t.stakes for t in result.created_tasks}
        assert plan_stakes == task_stakes
        assert task_stakes["only"] is Stakes.HIGH
