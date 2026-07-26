"""Tests for hybrid loop replanning behavior."""

from typing import TYPE_CHECKING

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid.replan_helpers import do_replan
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.plan_loop_context import ReplanTrigger

from ._hybrid_loop_helpers import (
    _content_filter_response,
    _ctx_with_user_msg,
    _make_plan_model,
    _multi_step_plan,
    _single_step_plan,
    _step_fail_response,
    _step_run_context,
    _step_run_state,
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
        """A failure-triggered replan prompt differs from a success one."""
        plan = _make_plan_model()
        step = plan.steps[0]
        cfg = HybridLoopConfig(max_replans=2)

        failure_provider = mock_provider_factory([_single_step_plan()])
        await do_replan(
            cfg,
            _step_run_context(failure_provider),
            _step_run_state(_ctx_with_user_msg(sample_agent_context), plan),
            step,
            trigger=ReplanTrigger.STEP_FAILURE,
        )
        failure_messages = failure_provider.recorded_messages[0]

        success_provider = mock_provider_factory([_single_step_plan()])
        await do_replan(
            cfg,
            _step_run_context(success_provider),
            _step_run_state(_ctx_with_user_msg(sample_agent_context), plan),
            step,
            trigger=ReplanTrigger.COMPLETION_SUMMARY,
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
class TestHybridLoopReplanRestartsTheWalk:
    """A replan must rewind the cursor and actually run the revised plan."""

    async def test_replan_after_a_later_step_reruns_from_the_new_first_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A failure at step 2 replans and executes the revised step 1.

        Driven from step index 1 on purpose: with a single-step plan a
        cursor left where it was would still land on a valid index and the
        run would terminate looking successful, so only a later-step failure
        distinguishes a real rewind from a no-op.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=1)
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # 3-step plan
                _stop_response("Step 1 done."),  # step index 0 succeeds
                _step_fail_response(),  # step index 1 fails
                _single_step_plan(),  # failure replan -> 1-step plan
                _stop_response("Revised step done."),  # revised step index 0
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1
        # The fifth call is the proof: a cursor left at index 1 would make
        # the one-step revised plan exit immediately after four calls.
        assert provider.call_count == 5
        assert len(result.turns) == 5
        revised_step_messages = provider.recorded_messages[4]
        assert any(
            "Analyze and solve the problem" in (m.content or "")
            for m in revised_step_messages
        )


@pytest.mark.unit
class TestHybridLoopFinalResult:
    """Terminal classification once the step walk stops."""

    async def test_turns_exhausted_mid_plan_terminates_max_turns(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Running out of turns with steps left is MAX_TURNS, not COMPLETED.

        Reached only with per-step checkpointing off: the progress summary
        otherwise short-circuits on its own turn check before the walk ends.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        ctx = ctx.model_copy(update={"max_turns": 2})
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=0)
        provider = mock_provider_factory(
            [
                _multi_step_plan(),  # turn 1: 3-step plan
                _stop_response("Step 1 done."),  # turn 2: step index 0 done
                # No turns left, steps 2 and 3 never start.
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.metadata["replans_used"] == 0
        final_plan = result.metadata["final_plan"]
        assert isinstance(final_plan, dict)
        # The synced history entry carries the live step statuses, so the
        # completed first step is visible on the terminal result.
        assert final_plan["steps"][0]["status"] == "completed"


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
