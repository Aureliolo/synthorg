"""Tests for the Plan-and-Execute execution loop."""

import json
from typing import TYPE_CHECKING, Any

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import ToolCategory
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.plan_models import PlanExecuteConfig
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=0.001,
    )


def _plan_response(steps: list[dict[str, Any]]) -> CompletionResponse:
    """Build a plan response with JSON-formatted steps."""
    plan = {"steps": steps}
    return CompletionResponse(
        content=json.dumps(plan),
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _single_step_plan() -> CompletionResponse:
    return _plan_response(
        [
            {
                "step_number": 1,
                "description": "Analyze and solve the problem",
                "expected_outcome": "Problem solved",
            },
        ]
    )


def _multi_step_plan() -> CompletionResponse:
    return _plan_response(
        [
            {
                "step_number": 1,
                "description": "Research the topic",
                "expected_outcome": "Understanding gained",
            },
            {
                "step_number": 2,
                "description": "Implement solution",
                "expected_outcome": "Code written",
            },
            {
                "step_number": 3,
                "description": "Verify results",
                "expected_outcome": "Tests pass",
            },
        ]
    )


def _stop_response(content: str = "Done.") -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _tool_use_response(
    tool_name: str = "echo",
    tool_call_id: str = "tc-1",
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id=tool_call_id, name=tool_name, arguments={}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


def _content_filter_response() -> CompletionResponse:
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.CONTENT_FILTER,
        usage=_usage(),
        model="test-model-001",
    )


def _step_fail_response() -> CompletionResponse:
    """Response causing step failure (TOOL_USE with no tool calls).

    Passes ``check_response_errors`` (not CONTENT_FILTER/ERROR) but
    ``_assess_step_success`` returns False (TOOL_USE ≠ STOP/MAX_TOKENS).
    """
    return CompletionResponse(
        content="I could not complete this step.",
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


class _StubTool(BaseTool):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Test tool",
            category=ToolCategory.CODE_EXECUTION,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"echoed: {arguments}",
            is_error=False,
        )


def _make_invoker(*tool_names: str) -> ToolInvoker:
    tools = [_StubTool(name=n) for n in tool_names]
    return ToolInvoker(ToolRegistry(tools))


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    msg = ChatMessage(role=MessageRole.USER, content="Do something")
    return ctx.with_message(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanExecuteLoopBasic:
    """Single-step plan → execute → complete."""

    async def test_single_step_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Step 1 done."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 2  # plan + execution
        assert result.metadata["loop_type"] == "plan_execute"
        assert result.metadata["replans_used"] == 0
        assert result.metadata["final_plan"] is not None
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 1
        # Verify call categories: planning = SYSTEM, execution = PRODUCTIVE
        assert result.turns[0].call_category == LLMCallCategory.SYSTEM
        assert result.turns[1].call_category == LLMCallCategory.PRODUCTIVE

    async def test_multi_step_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _multi_step_plan(),
                _stop_response("Research done."),
                _stop_response("Implementation done."),
                _stop_response("Verification done."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert len(result.turns) == 4  # plan + 3 steps


@pytest.mark.unit
class TestPlanExecuteLoopWithTools:
    """Steps that invoke tools."""

    async def test_tool_calls_per_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _tool_use_response("echo", "tc-1"),
                _stop_response("Tool used and done."),
            ]
        )
        invoker = _make_invoker("echo")
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1
        assert len(result.turns) == 3  # plan + tool_use + stop


@pytest.mark.unit
class TestPlanExecuteLoopReplanning:
    """Re-planning on step failure."""

    async def test_content_filter_during_step_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        # Plan: 1 step, step returns content_filter → immediate ERROR
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _content_filter_response(),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )
        assert result.termination_reason == TerminationReason.ERROR

    async def test_max_replans_exhausted(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step fails non-terminally but max_replans=0 blocks replanning."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                # Step fails via TOOL_USE with no tool_calls (passes
                # check_response_errors, but _assess_step_success → False)
                _step_fail_response(),
            ]
        )
        loop = PlanExecuteLoop(PlanExecuteConfig(max_replans=0))

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "Max replans" in (result.error_message or "")
        assert result.metadata["loop_type"] == "plan_execute"
        assert result.metadata["replans_used"] == 0

    async def test_successful_replan_completes(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step fails, replan produces new plan, second attempt succeeds."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # Initial plan: 1 step
                _step_fail_response(),  # Step 1 fails (non-terminal)
                _single_step_plan(),  # Replan: new 1-step plan
                _stop_response("Fixed it."),  # New step 1 succeeds
            ]
        )
        loop = PlanExecuteLoop(PlanExecuteConfig(max_replans=2))

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 2  # original + 1 replan


@pytest.mark.unit
class TestPlanExecuteLoopBudget:
    """Budget exhaustion during planning and execution."""

    async def test_budget_exhausted_before_planning(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=lambda _: True,
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert provider.call_count == 0

    async def test_budget_exhausted_during_step_execution(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        def budget_check(_: AgentContext) -> bool:
            nonlocal call_count
            call_count += 1
            # Budget checks: (1) before plan, (2) in step mini-ReAct
            # Exhaust on the second check — during step execution
            return call_count > 1

        provider = mock_provider_factory(
            [
                _single_step_plan(),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=budget_check,
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED


@pytest.mark.unit
class TestPlanExecuteLoopShutdown:
    """Shutdown during planning and execution."""

    async def test_shutdown_before_planning(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=lambda: True,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN
        assert provider.call_count == 0

    async def test_shutdown_during_step_execution(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        def shutdown_check() -> bool:
            nonlocal call_count
            call_count += 1
            # Shutdown checks: (1) before plan, (2) in step mini-ReAct
            # Trigger on second check — during step execution
            return call_count > 1

        provider = mock_provider_factory(
            [
                _single_step_plan(),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=shutdown_check,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN


@pytest.mark.unit
class TestPlanExecuteLoopMaxTurns:
    """Turn limit hit during step execution."""

    async def test_max_turns_during_step(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            max_turns=2,
        )
        ctx = _ctx_with_user_msg(ctx)

        # Plan takes 1 turn, multi-step needs more
        provider = mock_provider_factory(
            [
                _multi_step_plan(),
                _stop_response("Step 1 done."),
                # No more turns available for step 2
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        # Plan uses turn 1, step 1 uses turn 2; no turns left for steps 2-3
        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.metadata["loop_type"] == "plan_execute"


@pytest.mark.unit
class TestPlanExecuteLoopModelTiering:
    """Model tiering: planner_model != executor_model."""

    async def test_different_models_for_phases(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Step done."),
            ]
        )
        config = PlanExecuteConfig(
            planner_model="test-planner-001",
            executor_model="test-executor-001",
        )
        loop = PlanExecuteLoop(config)

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert provider.call_count == 2
        # Verify planning used planner_model and execution used executor_model
        assert provider.recorded_models[0] == "test-planner-001"
        assert provider.recorded_models[1] == "test-executor-001"


@pytest.mark.unit
class TestPlanExecuteLoopPlanParsing:
    """Plan parse error handling."""

    async def test_unparseable_plan_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        bad_response = CompletionResponse(
            content="I don't know how to make a plan.",
            finish_reason=FinishReason.STOP,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory([bad_response])
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "parse" in (result.error_message or "").lower()

    async def test_markdown_code_fence_json(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        plan_json = json.dumps(
            {
                "steps": [
                    {
                        "step_number": 1,
                        "description": "Do the thing",
                        "expected_outcome": "Thing done",
                    },
                ],
            }
        )
        fenced_response = CompletionResponse(
            content=f"```json\n{plan_json}\n```",
            finish_reason=FinishReason.STOP,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory(
            [
                fenced_response,
                _stop_response("Step done."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_text_plan_fallback(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        text_response = CompletionResponse(
            content="1. Research the topic\n2. Write the code\n3. Test it",
            finish_reason=FinishReason.STOP,
            usage=_usage(),
            model="test-model-001",
        )
        provider = mock_provider_factory(
            [
                text_response,
                _stop_response("Research done."),
                _stop_response("Code written."),
                _stop_response("Tests pass."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) >= 1


@pytest.mark.unit
class TestPlanExecuteLoopMetadata:
    """Plan stored in metadata."""

    async def test_metadata_structure(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert "loop_type" in result.metadata
        assert result.metadata["loop_type"] == "plan_execute"
        assert "plans" in result.metadata
        assert "final_plan" in result.metadata
        assert "replans_used" in result.metadata
        assert isinstance(result.metadata["plans"], list)
        assert len(result.metadata["plans"]) == 1


@pytest.mark.unit
class TestPlanExecuteLoopContextImmutability:
    """Original context unchanged after execution."""

    async def test_original_context_unchanged(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        original_turn_count = ctx.turn_count
        original_conv_len = len(ctx.conversation)

        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
            ]
        )
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
        )

        assert ctx.turn_count == original_turn_count
        assert len(ctx.conversation) == original_conv_len
        assert result.context.turn_count > original_turn_count


@pytest.mark.unit
class TestPlanExecuteLoopProviderException:
    """Provider exception during planning."""

    async def test_provider_error_during_planning(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        class _FailingProvider:
            async def complete(self, *_a: Any, **_kw: Any) -> None:
                msg = "connection refused"
                raise ConnectionError(msg)

        loop = PlanExecuteLoop()
        result = await loop.execute(
            context=ctx,
            provider=_FailingProvider(),  # type: ignore[arg-type]
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert result.error_message is not None
        assert "ConnectionError" in result.error_message

    async def test_provider_error_during_step_execution(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        call_count = 0

        class _PartialProvider:
            """Returns plan on first call, errors on second."""

            async def complete(self, *_a: Any, **_kw: Any) -> Any:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return _single_step_plan()
                msg = "model overloaded"
                raise ConnectionError(msg)

        loop = PlanExecuteLoop()
        result = await loop.execute(
            context=ctx,
            provider=_PartialProvider(),  # type: ignore[arg-type]
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "ConnectionError" in (result.error_message or "")


@pytest.mark.unit
class TestPlanExecuteLoopProtocol:
    """Protocol conformance."""

    def test_is_execution_loop(self) -> None:
        from synthorg.engine.loop_protocol import ExecutionLoop

        loop = PlanExecuteLoop()
        assert isinstance(loop, ExecutionLoop)

    def test_loop_type(self) -> None:
        loop = PlanExecuteLoop()
        assert loop.get_loop_type() == "plan_execute"

    def test_custom_config(self) -> None:
        config = PlanExecuteConfig(max_replans=5)
        loop = PlanExecuteLoop(config)
        assert loop.get_loop_type() == "plan_execute"


@pytest.mark.unit
class TestPlanExecuteMultiStepWithTools:
    """Multi-step plan where steps use tools — integration-style test."""

    async def test_multi_step_with_tool_calls(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Two-step plan: step 1 uses a tool, step 2 completes directly."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        two_step = _plan_response(
            [
                {
                    "step_number": 1,
                    "description": "Search the codebase",
                    "expected_outcome": "Relevant files identified",
                },
                {
                    "step_number": 2,
                    "description": "Summarize findings",
                    "expected_outcome": "Summary written",
                },
            ]
        )
        provider = mock_provider_factory(
            [
                two_step,  # Plan
                _tool_use_response("echo", "tc-1"),  # Step 1: tool call
                _stop_response("Found the files."),  # Step 1: complete
                _stop_response("Here is the summary."),  # Step 2: complete
            ]
        )
        invoker = _make_invoker("echo")
        loop = PlanExecuteLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1
        assert len(result.turns) == 4  # plan + tool_use + step1_stop + step2
        assert result.metadata["replans_used"] == 0
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 1


@pytest.mark.unit
class TestReactVsPlanExecuteComparison:
    """Compare ReactLoop and PlanExecuteLoop on the same task."""

    async def test_both_loops_complete_same_task(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Both loops reach COMPLETED on a simple task."""
        from synthorg.engine.react_loop import ReactLoop

        ctx = _ctx_with_user_msg(sample_agent_context)

        # ReactLoop: single LLM call → done
        react_provider = mock_provider_factory([_stop_response("Task complete.")])
        react_result = await ReactLoop().execute(
            context=ctx,
            provider=react_provider,
        )

        # PlanExecuteLoop: plan + execute step → done
        pe_provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Task complete."),
            ]
        )
        pe_result = await PlanExecuteLoop().execute(
            context=ctx,
            provider=pe_provider,
        )

        # Both complete successfully
        assert react_result.termination_reason == TerminationReason.COMPLETED
        assert pe_result.termination_reason == TerminationReason.COMPLETED

        # PlanExecuteLoop uses more turns (plan + execution)
        assert len(pe_result.turns) > len(react_result.turns)

        # PlanExecuteLoop has plan metadata, ReactLoop does not
        assert "plans" in pe_result.metadata
        assert "plans" not in react_result.metadata
