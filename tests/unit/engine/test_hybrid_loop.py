"""Tests for the Hybrid Plan + ReAct execution loop.

Core tests: protocol, basic execution, tools, step turns, progress
summary, budget, shutdown, max turns, and plan parsing.

Replanning tests are in ``test_hybrid_loop_replanning.py``.
Advanced tests (stagnation, tiering, metadata, immutability, checkpoint,
compaction, replan parsing, provider errors) are in
``test_hybrid_loop_advanced.py``.
"""

from typing import TYPE_CHECKING

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.providers.models import CompletionResponse

from ._hybrid_loop_helpers import (
    _ctx_with_user_msg,
    _make_invoker,
    _multi_step_plan,
    _single_step_plan,
    _stop_response,
    _summary_response,
    _tool_use_response,
    _usage,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHybridLoopProtocol:
    """Protocol compliance and basic properties."""

    def test_loop_type(self) -> None:
        loop = HybridLoop()
        assert loop.get_loop_type() == "hybrid"

    def test_is_execution_loop(self) -> None:
        from synthorg.engine.loop_protocol import ExecutionLoop

        loop = HybridLoop()
        assert isinstance(loop, ExecutionLoop)

    def test_default_config(self) -> None:
        loop = HybridLoop()
        assert loop.config.max_plan_steps == 7
        assert loop.config.max_turns_per_step == 5

    def test_custom_config(self) -> None:
        cfg = HybridLoopConfig(max_plan_steps=3, max_turns_per_step=10)
        loop = HybridLoop(config=cfg)
        assert loop.config.max_plan_steps == 3
        assert loop.config.max_turns_per_step == 10


@pytest.mark.unit
class TestHybridLoopBasic:
    """Single-step and multi-step plan -> execute -> complete."""

    async def test_single_step_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning
                _stop_response("Done."),  # step 1 execution
                _summary_response(),  # progress summary
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # 3 turns: plan + step execution + summary
        assert len(result.turns) == 3
        assert result.metadata["loop_type"] == "hybrid"
        assert result.metadata["replans_used"] == 0
        # Planning = SYSTEM, execution = PRODUCTIVE, summary = SYSTEM
        assert result.turns[0].call_category == LLMCallCategory.SYSTEM
        assert result.turns[1].call_category == LLMCallCategory.PRODUCTIVE
        assert result.turns[2].call_category == LLMCallCategory.SYSTEM

    async def test_multi_step_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # planning
                _stop_response("Research done."),  # step 1
                _summary_response(),  # summary 1
                _stop_response("Implementation done."),  # step 2
                _summary_response(),  # summary 2
                _stop_response("Verification done."),  # step 3
                _summary_response(),  # summary 3
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # 7 turns: plan + 3*(step + summary)
        assert len(result.turns) == 7

    async def test_no_summary_when_disabled(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """When checkpoint_after_each_step=False, skip progress summary."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning
                _stop_response("Done."),  # step 1 execution
            ]
        )
        cfg = HybridLoopConfig(checkpoint_after_each_step=False)
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # 2 turns: plan + step execution (no summary)
        assert len(result.turns) == 2


@pytest.mark.unit
class TestHybridLoopWithTools:
    """Steps that invoke tools."""

    async def test_tool_calls_per_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning
                _tool_use_response("echo", "tc-1"),  # step 1 turn 1
                _stop_response("Tool used and done."),  # step 1 turn 2
                _summary_response(),  # summary
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_tool_calls == 1
        # 4 turns: plan + tool_use + stop + summary
        assert len(result.turns) == 4


@pytest.mark.unit
class TestHybridLoopPerStepTurnLimit:
    """Per-step turn limiting (unique to hybrid)."""

    async def test_step_fails_on_turn_limit(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step uses all max_turns_per_step without completing -> FAILED."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            max_turns_per_step=2,
            max_replans=0,
        )
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning
                _tool_use_response("echo", "tc-1"),  # step turn 1
                _tool_use_response("echo", "tc-2"),  # step turn 2 (limit!)
                # step fails, replans exhausted -> ERROR
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop(config=cfg)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.ERROR
        assert "Max replans" in (result.error_message or "")

    async def test_step_succeeds_within_limit(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step completes before per-step limit."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(max_turns_per_step=3)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning
                _tool_use_response("echo", "tc-1"),  # step turn 1
                _stop_response("Done after tool use."),  # step turn 2
                _summary_response(),  # summary
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop(config=cfg)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestHybridLoopProgressSummary:
    """Progress summary and LLM-decided replanning."""

    async def test_summary_triggers_replan(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """LLM says replan=true after step 1 -> creates a new plan."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(allow_replan_on_completion=True)
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # initial plan (3 steps)
                _stop_response("Research done."),  # step 1 execution
                _summary_response(replan=True),  # summary -> replan!
                _single_step_plan(),  # new plan (1 step)
                _stop_response("All done."),  # new step 1
                _summary_response(replan=False),  # summary -> no replan
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 2  # original + replanned

    async def test_no_replan_when_disabled(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """allow_replan_on_completion=False ignores replan signal."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(allow_replan_on_completion=False)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                # Summary says replan, but config says no
                _summary_response(replan=True),
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 0


@pytest.mark.unit
class TestHybridLoopBudget:
    """Budget exhaustion handling."""

    async def test_budget_exhausted_before_planning(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=lambda _ctx: True,
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED

    async def test_budget_exhausted_during_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        call_count = 0

        def budget_check(_ctx: AgentContext) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count > 1  # allow planning, block step

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            budget_checker=budget_check,
        )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED


@pytest.mark.unit
class TestHybridLoopShutdown:
    """Shutdown handling."""

    async def test_shutdown_before_planning(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([])
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=lambda: True,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN

    async def test_shutdown_during_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        call_count = 0

        def shutdown_check() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count > 1

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            shutdown_checker=shutdown_check,
        )

        assert result.termination_reason == TerminationReason.SHUTDOWN


@pytest.mark.unit
class TestHybridLoopMaxTurns:
    """Global turn budget exhaustion."""

    async def test_max_turns_during_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Run out of global turns mid-step -> MAX_TURNS."""
        # Create context with very low max_turns
        ctx = _ctx_with_user_msg(sample_agent_context)
        ctx = ctx.model_copy(update={"max_turns": 2})
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # turn 1
                _tool_use_response("echo", "tc-1"),  # turn 2 (max!)
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.MAX_TURNS


@pytest.mark.unit
class TestHybridLoopPlanParsing:
    """Plan parsing edge cases."""

    async def test_unparseable_plan_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                CompletionResponse(
                    content="This is not a plan.",
                    finish_reason=FinishReason.STOP,
                    usage=_usage(),
                    model="test-model-001",
                ),
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert "parse" in (result.error_message or "").lower()

    async def test_plan_truncated_to_max_steps(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Plan with more steps than max_plan_steps gets truncated."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(max_plan_steps=2)
        # LLM returns a 3-step plan, but config says max 2
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # 3 steps, truncated to 2
                _stop_response("Step 1 done."),  # step 1
                _summary_response(),  # summary 1
                _stop_response("Step 2 done."),  # step 2
                _summary_response(),  # summary 2
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # Only 2 steps executed (not 3)
        final_plan = result.metadata["final_plan"]
        assert isinstance(final_plan, dict)
        assert len(final_plan["steps"]) == 2
