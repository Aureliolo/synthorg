"""Tests for LLM decomposition strategy."""

import json
from typing import Any

import pytest

from synthorg.core.enums import (
    CoordinationTopology,
    Priority,
    TaskStructure,
    TaskType,
)
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.decomposition.llm import (
    LlmDecompositionConfig,
    LlmDecompositionStrategy,
)
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
)
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.errors import DecompositionDepthError, DecompositionError
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)

from .conftest import MockCompletionProvider


def _make_task(
    task_id: str = "task-llm-1",
    *,
    title: str = "Build authentication",
    description: str = "Implement JWT auth for the REST API.",
) -> Task:
    """Create a minimal task for LLM decomposition tests."""
    return Task(
        id=task_id,
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="creator",
        acceptance_criteria=(AcceptanceCriterion(description="Login returns token"),),
    )


def _make_context(
    max_subtasks: int = 10,
    max_depth: int = 3,
    current_depth: int = 0,
) -> DecompositionContext:
    """Create a decomposition context."""
    return DecompositionContext(
        max_subtasks=max_subtasks,
        max_depth=max_depth,
        current_depth=current_depth,
    )


def _valid_plan_args(
    *,
    subtask_count: int = 2,
    task_structure: str = "sequential",
    coordination_topology: str = "auto",
) -> dict[str, Any]:
    """Build valid tool call arguments for a decomposition plan."""
    subtasks = [
        {
            "id": f"sub-{i}",
            "title": f"Subtask {i}",
            "description": f"Do step {i}",
            "dependencies": [] if i == 0 else [f"sub-{i - 1}"],
            "estimated_complexity": "medium",
            "required_skills": ["python"],
        }
        for i in range(subtask_count)
    ]
    return {
        "subtasks": subtasks,
        "task_structure": task_structure,
        "coordination_topology": coordination_topology,
    }


def _make_tool_call_response(
    arguments: dict[str, Any],
    *,
    tool_name: str = "submit_decomposition_plan",
) -> CompletionResponse:
    """Create a CompletionResponse with a tool call."""
    return CompletionResponse(
        tool_calls=(
            ToolCall(
                id="tc-1",
                name=tool_name,
                arguments=arguments,
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.02,
        ),
        model="test-model-001",
    )


def _make_content_response(content: str) -> CompletionResponse:
    """Create a CompletionResponse with text content."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.02,
        ),
        model="test-model-001",
    )


class TestLlmDecompositionStrategy:
    """Tests for LlmDecompositionStrategy."""

    @pytest.mark.unit
    async def test_happy_path_tool_call(self) -> None:
        """Tool call response produces a valid plan."""
        args = _valid_plan_args()
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert plan.parent_task_id == "task-llm-1"
        assert len(plan.subtasks) == 2
        assert plan.task_structure is TaskStructure.SEQUENTIAL
        assert plan.coordination_topology is CoordinationTopology.AUTO
        assert provider.call_count == 1

    @pytest.mark.unit
    async def test_happy_path_content_fallback(self) -> None:
        """Content-only response is parsed as JSON fallback."""
        args = _valid_plan_args(subtask_count=1)
        content = json.dumps(args)
        response = _make_content_response(content)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert len(plan.subtasks) == 1

    @pytest.mark.unit
    async def test_depth_exceeded_no_provider_call(self) -> None:
        """Depth exceeded raises without calling the provider."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context(current_depth=3, max_depth=3)

        with pytest.raises(
            DecompositionDepthError,
            match="meets or exceeds max depth",
        ):
            await strategy.decompose(task, ctx)

        assert provider.call_count == 0

    @pytest.mark.unit
    async def test_max_subtasks_exceeded_raises(self) -> None:
        """Plan with too many subtasks exhausts retries."""
        args = _valid_plan_args(subtask_count=5)
        # Provide enough responses for 1 + max_retries attempts
        responses = [_make_tool_call_response(args) for _ in range(3)]
        provider = MockCompletionProvider(responses)
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context(max_subtasks=3)

        with pytest.raises(DecompositionError, match="retries exhausted"):
            await strategy.decompose(task, ctx)

        assert provider.call_count == 3

    @pytest.mark.unit
    async def test_malformed_json_retry_success(self) -> None:
        """Malformed response triggers retry; second attempt succeeds."""
        bad_response = _make_content_response("{invalid json")
        good_args = _valid_plan_args(subtask_count=1)
        good_response = _make_tool_call_response(good_args)
        provider = MockCompletionProvider([bad_response, good_response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert provider.call_count == 2

    @pytest.mark.unit
    async def test_all_retries_exhausted(self) -> None:
        """All retries exhausted raises DecompositionError."""
        bad_responses = [_make_content_response("{bad}") for _ in range(3)]
        provider = MockCompletionProvider(bad_responses)
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        with pytest.raises(DecompositionError, match="retries exhausted"):
            await strategy.decompose(task, ctx)

        # 1 initial + 2 retries = 3 calls
        assert provider.call_count == 3

    @pytest.mark.unit
    async def test_empty_response_raises(self) -> None:
        """Response with no content and no tool calls raises."""
        # A content_filter response has no content or tool calls
        empty_response = CompletionResponse(
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=0,
                cost_usd=0.0,
            ),
            model="test-model-001",
        )
        provider = MockCompletionProvider(
            [empty_response, empty_response, empty_response]
        )
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        with pytest.raises(DecompositionError):
            await strategy.decompose(task, ctx)

    @pytest.mark.unit
    async def test_provider_error_propagates(self) -> None:
        """Provider errors propagate without being caught."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        # MockCompletionProvider raises IndexError when empty
        with pytest.raises(IndexError):
            await strategy.decompose(task, ctx)

    @pytest.mark.unit
    def test_protocol_conformance(self) -> None:
        """LlmDecompositionStrategy satisfies DecompositionStrategy."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        assert isinstance(strategy, DecompositionStrategy)

    @pytest.mark.unit
    def test_strategy_name(self) -> None:
        """Strategy name is 'llm'."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        assert strategy.get_strategy_name() == "llm"

    @pytest.mark.unit
    async def test_temperature_passed_to_provider(self) -> None:
        """Temperature from config is passed to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        config = LlmDecompositionConfig(temperature=0.7)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        recorded = provider.recorded_configs
        assert len(recorded) == 1
        assert recorded[0] is not None
        assert recorded[0].temperature == 0.7

    @pytest.mark.unit
    async def test_custom_config_values(self) -> None:
        """Custom config values are respected."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        config = LlmDecompositionConfig(
            max_retries=5,
            temperature=1.0,
            max_output_tokens=2048,
        )
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        recorded = provider.recorded_configs
        assert recorded[0] is not None
        assert recorded[0].temperature == 1.0
        assert recorded[0].max_tokens == 2048

    @pytest.mark.unit
    async def test_model_passed_to_provider(self) -> None:
        """Model name is forwarded to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-large-001")
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        assert provider.recorded_models == ["test-large-001"]

    @pytest.mark.unit
    async def test_tool_definition_sent_to_provider(self) -> None:
        """Tool definition is sent to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        tools = provider.recorded_tools
        assert len(tools) == 1
        assert tools[0] is not None
        assert len(tools[0]) == 1
        assert tools[0][0].name == "submit_decomposition_plan"

    @pytest.mark.unit
    def test_blank_model_rejected(self) -> None:
        """Blank model string raises ValueError."""
        provider = MockCompletionProvider([])
        with pytest.raises(ValueError, match="non-blank"):
            LlmDecompositionStrategy(provider=provider, model="")

    @pytest.mark.unit
    def test_whitespace_model_rejected(self) -> None:
        """Whitespace-only model string raises ValueError."""
        provider = MockCompletionProvider([])
        with pytest.raises(ValueError, match="non-blank"):
            LlmDecompositionStrategy(provider=provider, model="   ")
