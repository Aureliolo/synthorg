"""Tests for Plan-and-Execute data models."""

import pytest
from pydantic import ValidationError

from synthorg.engine.plan_models import (
    ExecutionPlan,
    PlanExecuteConfig,
    PlanStep,
    StepStatus,
)


@pytest.mark.unit
class TestStepStatus:
    """StepStatus enum values."""

    def test_values(self) -> None:
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.IN_PROGRESS.value == "in_progress"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"

    def test_member_count(self) -> None:
        assert len(StepStatus) == 5


@pytest.mark.unit
class TestPlanStep:
    """PlanStep frozen model."""

    def test_creation(self) -> None:
        step = PlanStep(
            step_number=1,
            description="Analyze the codebase",
            expected_outcome="List of relevant files identified",
        )
        assert step.step_number == 1
        assert step.description == "Analyze the codebase"
        assert step.expected_outcome == "List of relevant files identified"
        assert step.status == StepStatus.PENDING
        assert step.actual_outcome is None

    def test_with_status(self) -> None:
        step = PlanStep(
            step_number=2,
            description="Write tests",
            expected_outcome="Tests pass",
            status=StepStatus.COMPLETED,
            actual_outcome="All 5 tests green",
        )
        assert step.status == StepStatus.COMPLETED
        assert step.actual_outcome == "All 5 tests green"

    def test_frozen(self) -> None:
        step = PlanStep(
            step_number=1,
            description="Do something",
            expected_outcome="Something done",
        )
        with pytest.raises(ValidationError):
            step.status = StepStatus.COMPLETED  # type: ignore[misc]

    def test_zero_step_number_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=0,
                description="Invalid",
                expected_outcome="Nope",
            )

    def test_negative_step_number_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=-1,
                description="Invalid",
                expected_outcome="Nope",
            )

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=1,
                description="",
                expected_outcome="Something",
            )

    def test_whitespace_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            PlanStep(
                step_number=1,
                description="   ",
                expected_outcome="Something",
            )

    def test_empty_expected_outcome_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=1,
                description="Valid desc",
                expected_outcome="",
            )

    def test_model_copy_update(self) -> None:
        step = PlanStep(
            step_number=1,
            description="Task",
            expected_outcome="Done",
        )
        updated = step.model_copy(
            update={"status": StepStatus.COMPLETED, "actual_outcome": "OK"},
        )
        assert updated.status == StepStatus.COMPLETED
        assert updated.actual_outcome == "OK"
        assert step.status == StepStatus.PENDING  # original unchanged


@pytest.mark.unit
class TestExecutionPlan:
    """ExecutionPlan frozen model."""

    def test_single_step(self) -> None:
        plan = ExecutionPlan(
            steps=(
                PlanStep(
                    step_number=1,
                    description="Do it",
                    expected_outcome="Done",
                ),
            ),
            original_task_summary="Simple task",
        )
        assert len(plan.steps) == 1
        assert plan.revision_number == 0
        assert plan.original_task_summary == "Simple task"

    def test_multi_step(self) -> None:
        steps = tuple(
            PlanStep(
                step_number=i,
                description=f"Step {i}",
                expected_outcome=f"Result {i}",
            )
            for i in range(1, 4)
        )
        plan = ExecutionPlan(
            steps=steps,
            original_task_summary="Multi-step task",
        )
        assert len(plan.steps) == 3

    def test_with_revision(self) -> None:
        plan = ExecutionPlan(
            steps=(
                PlanStep(
                    step_number=1,
                    description="Revised step",
                    expected_outcome="Better result",
                ),
            ),
            revision_number=2,
            original_task_summary="Revised task",
        )
        assert plan.revision_number == 2

    def test_frozen(self) -> None:
        plan = ExecutionPlan(
            steps=(
                PlanStep(
                    step_number=1,
                    description="Do it",
                    expected_outcome="Done",
                ),
            ),
            original_task_summary="Task",
        )
        with pytest.raises(ValidationError):
            plan.revision_number = 1  # type: ignore[misc]

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionPlan(
                steps=(),
                original_task_summary="Task",
            )

    def test_non_sequential_step_numbers_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="sequential",
        ):
            ExecutionPlan(
                steps=(
                    PlanStep(
                        step_number=1,
                        description="First",
                        expected_outcome="A",
                    ),
                    PlanStep(
                        step_number=3,
                        description="Third",
                        expected_outcome="C",
                    ),
                ),
                original_task_summary="Task",
            )

    def test_step_numbers_not_starting_at_one(self) -> None:
        with pytest.raises(ValidationError, match="sequential"):
            ExecutionPlan(
                steps=(
                    PlanStep(
                        step_number=2,
                        description="Should be 1",
                        expected_outcome="A",
                    ),
                ),
                original_task_summary="Task",
            )

    def test_negative_revision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionPlan(
                steps=(
                    PlanStep(
                        step_number=1,
                        description="Step",
                        expected_outcome="Done",
                    ),
                ),
                revision_number=-1,
                original_task_summary="Task",
            )

    def test_empty_task_summary_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionPlan(
                steps=(
                    PlanStep(
                        step_number=1,
                        description="Step",
                        expected_outcome="Done",
                    ),
                ),
                original_task_summary="",
            )

    def test_json_roundtrip(self) -> None:
        plan = ExecutionPlan(
            steps=(
                PlanStep(
                    step_number=1,
                    description="Analyze",
                    expected_outcome="Analysis done",
                ),
                PlanStep(
                    step_number=2,
                    description="Implement",
                    expected_outcome="Code written",
                ),
            ),
            revision_number=1,
            original_task_summary="Build feature",
        )
        json_str = plan.model_dump_json()
        restored = ExecutionPlan.model_validate_json(json_str)
        assert restored == plan


@pytest.mark.unit
class TestPlanExecuteConfig:
    """PlanExecuteConfig frozen model."""

    def test_defaults(self) -> None:
        config = PlanExecuteConfig()
        assert config.planner_model is None
        assert config.executor_model is None
        assert config.max_replans == 3

    def test_custom_values(self) -> None:
        config = PlanExecuteConfig(
            planner_model="test-large-001",
            executor_model="test-small-001",
            max_replans=5,
        )
        assert config.planner_model == "test-large-001"
        assert config.executor_model == "test-small-001"
        assert config.max_replans == 5

    def test_frozen(self) -> None:
        config = PlanExecuteConfig()
        with pytest.raises(ValidationError):
            config.max_replans = 10  # type: ignore[misc]

    def test_max_replans_zero(self) -> None:
        config = PlanExecuteConfig(max_replans=0)
        assert config.max_replans == 0

    def test_max_replans_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanExecuteConfig(max_replans=-1)

    def test_max_replans_exceeds_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanExecuteConfig(max_replans=11)

    def test_empty_planner_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlanExecuteConfig(planner_model="")

    def test_whitespace_executor_model_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            PlanExecuteConfig(executor_model="   ")
