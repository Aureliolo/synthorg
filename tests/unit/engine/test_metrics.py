"""Unit tests for TaskCompletionMetrics model."""

import pytest
from pydantic import ValidationError

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.metrics import TaskCompletionMetrics
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import TokenUsage


@pytest.mark.unit
class TestTaskCompletionMetricsConstruction:
    """Basic construction and frozen enforcement."""

    def test_valid_construction(self) -> None:
        metrics = TaskCompletionMetrics(
            task_id="task-001",
            agent_id="agent-001",
            turns_per_task=3,
            tokens_per_task=1500,
            cost_per_task=0.05,
            duration_seconds=12.5,
            prompt_tokens=150,
        )
        assert metrics.task_id == "task-001"
        assert metrics.agent_id == "agent-001"
        assert metrics.turns_per_task == 3
        assert metrics.tokens_per_task == 1500
        assert metrics.cost_per_task == 0.05
        assert metrics.duration_seconds == 12.5
        assert metrics.prompt_tokens == 150
        assert metrics.prompt_token_ratio == 0.1

    def test_prompt_fields_default_to_zero(self) -> None:
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=0,
            tokens_per_task=0,
            cost_per_task=0.0,
            duration_seconds=0.0,
        )
        assert metrics.prompt_tokens == 0
        assert metrics.prompt_token_ratio == 0.0

    def test_negative_prompt_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError, match="prompt_tokens"):
            TaskCompletionMetrics(
                agent_id="agent-001",
                turns_per_task=0,
                tokens_per_task=0,
                cost_per_task=0.0,
                duration_seconds=0.0,
                prompt_tokens=-1,
            )

    def test_prompt_token_ratio_is_computed(self) -> None:
        """prompt_token_ratio is derived from prompt_tokens / tokens_per_task."""
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=1,
            tokens_per_task=1000,
            cost_per_task=0.01,
            duration_seconds=1.0,
            prompt_tokens=500,
        )
        assert metrics.prompt_token_ratio == pytest.approx(0.5)

    def test_prompt_token_ratio_at_boundary(self) -> None:
        """When prompt_tokens == tokens_per_task, ratio is 1.0."""
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=1,
            tokens_per_task=100,
            cost_per_task=0.01,
            duration_seconds=1.0,
            prompt_tokens=100,
        )
        assert metrics.prompt_token_ratio == pytest.approx(1.0)

    def test_task_id_none(self) -> None:
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=0,
            tokens_per_task=0,
            cost_per_task=0.0,
            duration_seconds=0.0,
        )
        assert metrics.task_id is None

    def test_frozen(self) -> None:
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=1,
            tokens_per_task=100,
            cost_per_task=0.01,
            duration_seconds=1.0,
        )
        with pytest.raises(ValidationError):
            metrics.turns_per_task = 5  # type: ignore[misc]

    def test_zero_values(self) -> None:
        metrics = TaskCompletionMetrics(
            agent_id="agent-001",
            turns_per_task=0,
            tokens_per_task=0,
            cost_per_task=0.0,
            duration_seconds=0.0,
        )
        assert metrics.turns_per_task == 0
        assert metrics.tokens_per_task == 0

    def test_negative_turns_rejected(self) -> None:
        with pytest.raises(ValidationError, match="turns_per_task"):
            TaskCompletionMetrics(
                agent_id="agent-001",
                turns_per_task=-1,
                tokens_per_task=0,
                cost_per_task=0.0,
                duration_seconds=0.0,
            )

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tokens_per_task"):
            TaskCompletionMetrics(
                agent_id="agent-001",
                turns_per_task=0,
                tokens_per_task=-1,
                cost_per_task=0.0,
                duration_seconds=0.0,
            )

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id"):
            TaskCompletionMetrics(
                agent_id="  ",
                turns_per_task=0,
                tokens_per_task=0,
                cost_per_task=0.0,
                duration_seconds=0.0,
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cost_per_task"):
            TaskCompletionMetrics(
                agent_id="agent-001",
                turns_per_task=0,
                tokens_per_task=0,
                cost_per_task=-0.01,
                duration_seconds=0.0,
            )

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duration_seconds"):
            TaskCompletionMetrics(
                agent_id="agent-001",
                turns_per_task=0,
                tokens_per_task=0,
                cost_per_task=0.0,
                duration_seconds=-1.0,
            )

    def test_blank_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id"):
            TaskCompletionMetrics(
                task_id="  ",
                agent_id="agent-001",
                turns_per_task=0,
                tokens_per_task=0,
                cost_per_task=0.0,
                duration_seconds=0.0,
            )


@pytest.mark.unit
class TestTaskCompletionMetricsFromRunResult:
    """Factory method extracts values from AgentRunResult."""

    def _make_run_result(
        self,
        sample_agent_context: AgentContext,
        *,
        turns: tuple[TurnRecord, ...] = (),
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> AgentRunResult:
        """Build a minimal AgentRunResult for testing."""
        ctx = sample_agent_context
        if input_tokens or output_tokens:
            ctx = ctx.model_copy(
                update={
                    "accumulated_cost": TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                    ),
                },
            )
        execution_result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        prompt = SystemPrompt(
            content="test",
            template_version="v1",
            estimated_tokens=10,
            sections=(),
            metadata={},
        )
        return AgentRunResult(
            execution_result=execution_result,
            system_prompt=prompt,
            duration_seconds=5.0,
            agent_id=str(sample_agent_context.identity.id),
            task_id="task-001",
        )

    def test_from_run_result_extracts_values(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                finish_reason=FinishReason.STOP,
            ),
            TurnRecord(
                turn_number=2,
                input_tokens=200,
                output_tokens=80,
                cost_usd=0.02,
                finish_reason=FinishReason.STOP,
            ),
        )
        result = self._make_run_result(
            sample_agent_context,
            turns=turns,
            input_tokens=300,
            output_tokens=130,
            cost_usd=0.03,
        )
        metrics = TaskCompletionMetrics.from_run_result(result)

        assert metrics.task_id == "task-001"
        assert metrics.agent_id == str(
            sample_agent_context.identity.id,
        )
        assert metrics.turns_per_task == 2
        assert metrics.tokens_per_task == 430  # 300 + 130
        assert metrics.cost_per_task == 0.03
        assert metrics.duration_seconds == 5.0
        # Prompt tokens come from the SystemPrompt estimated_tokens (10).
        assert metrics.prompt_tokens == 10
        # 10 / 430 ≈ 0.0232...
        assert 0.02 < metrics.prompt_token_ratio < 0.03

    def test_from_run_result_zero_turns(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = self._make_run_result(sample_agent_context)
        metrics = TaskCompletionMetrics.from_run_result(result)

        assert metrics.turns_per_task == 0
        assert metrics.tokens_per_task == 0
        assert metrics.cost_per_task == 0.0
        # Zero total tokens → 0.0 ratio (no divide-by-zero).
        assert metrics.prompt_token_ratio == 0.0
        assert metrics.prompt_tokens == 10
