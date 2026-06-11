"""Unit tests for plan_helpers module -- shared plan utilities."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.plan_helpers import (
    assess_step_success,
    extract_task_summary,
    update_step_status,
)
from synthorg.engine.plan_models import ExecutionPlan, PlanStep, StepStatus
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(
    num_steps: int = 3,
    *,
    summary: str = "test task",
) -> ExecutionPlan:
    """Build an ExecutionPlan with *num_steps* PENDING steps."""
    steps = tuple(
        PlanStep(
            step_number=i + 1,
            description=f"Step {i + 1} description",
            expected_outcome=f"Outcome {i + 1}",
        )
        for i in range(num_steps)
    )
    return ExecutionPlan(
        steps=steps,
        original_task_summary=summary,
    )


def _make_response(
    finish_reason: FinishReason = FinishReason.STOP,
) -> CompletionResponse:
    """Build a minimal CompletionResponse with the given finish reason."""
    return CompletionResponse(
        content="Done.",
        finish_reason=finish_reason,
        usage=TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cost=0.001,
        ),
        model="test-model-001",
    )


# ---------------------------------------------------------------------------
# update_step_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateStepStatus:
    """Tests for update_step_status immutable step update."""

    def test_updates_correct_step_and_returns_new_plan(self) -> None:
        """Updating a step returns a new plan; original is unmodified."""
        plan = _make_plan(3)
        updated = update_step_status(plan, 1, StepStatus.IN_PROGRESS)

        # New plan has the update
        assert updated.steps[1].status == StepStatus.IN_PROGRESS
        # Other steps are unchanged
        assert updated.steps[0].status == StepStatus.PENDING
        assert updated.steps[2].status == StepStatus.PENDING
        # Original plan is not mutated (immutability)
        assert plan.steps[1].status == StepStatus.PENDING
        assert updated is not plan

    def test_first_index(self) -> None:
        """Updating step at index 0 works correctly."""
        plan = _make_plan(2)
        updated = update_step_status(plan, 0, StepStatus.COMPLETED)

        assert updated.steps[0].status == StepStatus.COMPLETED
        assert updated.steps[1].status == StepStatus.PENDING

    def test_last_index(self) -> None:
        """Updating the last step works correctly."""
        plan = _make_plan(4)
        updated = update_step_status(plan, 3, StepStatus.FAILED)

        assert updated.steps[3].status == StepStatus.FAILED
        # All preceding steps remain unchanged
        for i in range(3):
            assert updated.steps[i].status == StepStatus.PENDING

    def test_out_of_range_raises_index_error(self) -> None:
        """Out-of-range index raises IndexError with descriptive message."""
        plan = _make_plan(2)

        with pytest.raises(IndexError, match="step_idx 5 out of range"):
            update_step_status(plan, 5, StepStatus.COMPLETED)

    def test_negative_index_raises_index_error(self) -> None:
        """Negative index raises IndexError (bounds check)."""
        plan = _make_plan(3)

        with pytest.raises(IndexError, match="step_idx -1 out of range"):
            update_step_status(plan, -1, StepStatus.COMPLETED)


# ---------------------------------------------------------------------------
# extract_task_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractTaskSummary:
    """Tests for extract_task_summary context extraction."""

    def test_returns_task_title_when_task_execution_present(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """When task_execution is set, returns the task title."""
        assert sample_agent_context.task_execution is not None
        result = extract_task_summary(sample_agent_context)
        assert result == sample_agent_context.task_execution.task.title

    def test_returns_first_user_message_when_no_task(
        self,
        sample_agent_with_personality: object,
    ) -> None:
        """When no task_execution, returns the first user message."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,  # type: ignore[arg-type]
        )
        user_msg = ChatMessage(
            role=MessageRole.USER,
            content="Please analyze the codebase",
        )
        ctx = ctx.with_message(user_msg)

        result = extract_task_summary(ctx)
        assert result == "Please analyze the codebase"

    def test_returns_fallback_when_empty_conversation(
        self,
        sample_agent_with_personality: object,
    ) -> None:
        """When no task and no messages, returns 'task' fallback."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,  # type: ignore[arg-type]
        )

        result = extract_task_summary(ctx)
        assert result == "task"

    def test_truncation_at_200_chars(
        self,
        sample_agent_with_personality: object,
    ) -> None:
        """Long text is truncated to 200 characters."""
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,  # type: ignore[arg-type]
        )
        long_content = "A" * 300
        user_msg = ChatMessage(
            role=MessageRole.USER,
            content=long_content,
        )
        ctx = ctx.with_message(user_msg)

        result = extract_task_summary(ctx)
        assert len(result) == 200
        assert result == "A" * 200


# ---------------------------------------------------------------------------
# assess_step_success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAssessStepSuccess:
    """Tests for assess_step_success finish-reason classification."""

    @pytest.mark.parametrize(
        ("finish_reason", "expected"),
        [
            (FinishReason.STOP, True),
            (FinishReason.MAX_TOKENS, True),
            (FinishReason.TOOL_USE, False),
            (FinishReason.CONTENT_FILTER, False),
            (FinishReason.ERROR, False),
        ],
    )
    def test_finish_reason_classification(
        self,
        finish_reason: FinishReason,
        expected: bool,
    ) -> None:
        """Parametrized test across all FinishReason values."""
        response = _make_response(finish_reason)
        assert assess_step_success(response) is expected
