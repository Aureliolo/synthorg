"""Tests for hybrid loop replanning behavior."""

from typing import TYPE_CHECKING

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.providers.models import CompletionConfig

from ._hybrid_loop_helpers import (
    _ctx_with_user_msg,
    _make_plan_model,
    _multi_step_plan,
    _single_step_plan,
    _step_fail_response,
    _stop_response,
    _summary_response,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider


@pytest.mark.unit
class TestHybridLoopReplanning:
    """Re-planning on step failure."""

    async def test_max_replans_exhausted(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step fails, max_replans=0 -> ERROR."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(max_replans=0)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _step_fail_response(),
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert "Max replans" in (result.error_message or "")

    async def test_successful_replan_on_failure(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Step fails, replan succeeds, new plan completes."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(max_replans=1)
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # original plan
                _step_fail_response(),  # step fails
                _single_step_plan(),  # replan
                _stop_response("Done now."),  # new step succeeds
                _summary_response(),  # summary
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1

    async def test_content_filter_during_step_returns_error(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        from ._hybrid_loop_helpers import _content_filter_response

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _content_filter_response(),
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR


@pytest.mark.unit
class TestHybridLoopReplanPromptContent:
    """Verify replan prompt differs for success vs failure triggers."""

    async def test_do_replan_on_success_path(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """do_replan with step_failed=False produces a different prompt
        than step_failed=True, verifying the content differs for
        success vs failure triggers.
        """
        from synthorg.engine.hybrid.replan_helpers import do_replan

        plan = _make_plan_model()
        step = plan.steps[0]
        cfg = HybridLoopConfig(max_replans=2)

        default_config = CompletionConfig()

        # Capture messages for step_failed=True
        failure_provider = mock_provider_factory([_single_step_plan()])
        ctx_fail = _ctx_with_user_msg(sample_agent_context)
        await do_replan(
            cfg,
            ctx_fail,
            failure_provider,
            "test-model-001",
            default_config,
            plan,
            step,
            [],
            step_failed=True,
        )
        failure_messages = failure_provider.recorded_messages[0]

        # Capture messages for step_failed=False
        success_provider = mock_provider_factory([_single_step_plan()])
        ctx_ok = _ctx_with_user_msg(sample_agent_context)
        await do_replan(
            cfg,
            ctx_ok,
            success_provider,
            "test-model-001",
            default_config,
            plan,
            step,
            [],
            step_failed=False,
        )
        success_messages = success_provider.recorded_messages[0]

        # The replan message is the last user message in each call
        fail_prompt = failure_messages[-1].content or ""
        ok_prompt = success_messages[-1].content or ""

        # Both prompts should exist and differ
        assert fail_prompt
        assert ok_prompt
        assert fail_prompt != ok_prompt
        assert "failed" in fail_prompt.lower()
        assert "successfully" in ok_prompt.lower()


@pytest.mark.unit
class TestHybridLoopReplanBudgetShared:
    """Replan budget shared between failure and completion triggers."""

    async def test_replan_budget_shared_between_failure_and_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """max_replans applies across both failure and completion replans.

        After using 1 replan on completion, only max_replans-1 remain
        for failures.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            max_replans=1,
            allow_replan_on_completion=True,
        )
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # initial 3-step plan
                _stop_response("Step 1 done."),  # step 1 completes
                _summary_response(replan=True),  # triggers replan (uses 1)
                _single_step_plan(),  # new plan from completion replan
                _step_fail_response(),  # new step fails
                # max_replans exhausted (1 used on completion) -> ERROR
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.ERROR
        assert "Max replans" in (result.error_message or "")
        assert result.metadata["replans_used"] == 1

    async def test_last_step_no_replan_on_completion(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Completion-triggered replanning is skipped on the last step.

        When the last step completes, even if the LLM says replan=true,
        no replan occurs because there are no remaining steps.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            allow_replan_on_completion=True,
            max_replans=3,
        )
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # 1-step plan
                _stop_response("All done."),  # step 1 completes
                # Summary says replan, but it's the last step
                _summary_response(replan=True),
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # No replans used even though LLM requested one
        assert result.metadata["replans_used"] == 0
