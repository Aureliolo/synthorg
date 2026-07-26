"""Steering interaction coverage for the two plan-based loops.

The mid-turn REDIRECT re-issue, the steering replan at a step boundary, and
the compound case where a completion- or failure-triggered replan absorbs a
pending directive are all step-boundary behaviours that appear only when a
whole ``execute()`` runs. Driving them end to end is what pins the
``StepRunState`` write-backs those paths depend on: a helper that computes a
new context but forgets to store it back leaves the directive pending, the
replan counter short, or the cursor on the wrong step, none of which a
turn-count assertion would notice.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import ActiveSteeringDirective
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.plan_models import PlanExecuteConfig
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

from ._hybrid_loop_helpers import (
    _ctx_with_user_msg,
    _multi_step_plan,
    _single_step_plan,
    _step_fail_response,
    _stop_response,
    _summary_response,
)
from .conftest import MockCompletionProvider

pytestmark = pytest.mark.unit

_DIRECTIVE_ID = "steer-1"
_RECORDED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
_INTERRUPT_USAGE = TokenUsage(input_tokens=40, output_tokens=7, cost=0.004)


def _plan_count(plans: object) -> int:
    """Count the plan history entries a result carries.

    Returns:
        The number of plans recorded in ``metadata["plans"]``.
    """
    assert isinstance(plans, list)
    return len(plans)


def _redirect() -> ActiveSteeringDirective:
    return ActiveSteeringDirective(
        entry_id=NotBlankStr(_DIRECTIVE_ID),
        kind=InterventionKind.REDIRECT,
        text=NotBlankStr("switch the storage layer to Postgres"),
        author=NotBlankStr("mission-control"),
        recorded_at=_RECORDED_AT,
    )


class _RedirectInbox:
    """Yields one REDIRECT until the run records it as adopted.

    Honours ``already_adopted`` the way the brain-backed inbox does, so a
    loop that adopts the directive does not see it again on the next turn
    and the run terminates instead of replanning forever.
    """

    def __init__(self, *, first_poll_empty: bool = False) -> None:
        self.polls = 0
        self._first_poll_empty = first_poll_empty

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        del project_id, task_id, agent_id
        self.polls += 1
        if self._first_poll_empty and self.polls == 1:
            return ()
        if _DIRECTIVE_ID in already_adopted:
            return ()
        return (_redirect(),)


class _StreamingProvider(MockCompletionProvider):
    """Serves planner calls over ``complete`` and step turns over ``stream``.

    The chunk script never terminates the turn on its own past the first
    poll boundary, so a pending REDIRECT is what actually aborts the drain.
    """

    def __init__(self, responses: list[CompletionResponse]) -> None:
        super().__init__(responses)
        self.stream_calls = 0

    @override
    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Return a fresh chunk stream for one step turn.

        Returns:
            An async iterator over a short scripted completion.
        """
        del messages, model, tools, config
        self.stream_calls += 1

        async def _chunks() -> AsyncIterator[StreamChunk]:
            # Usage first, so the interrupt poll that runs after chunk 0 has
            # something to fold and the abort is not trivially free.
            yield StreamChunk(event_type=StreamEventType.USAGE, usage=_INTERRUPT_USAGE)
            yield StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content="Done.")
            yield StreamChunk(
                event_type=StreamEventType.DONE,
                finish_reason=FinishReason.STOP,
            )

        return _chunks()


@pytest.mark.unit
class TestHybridSteeringReplan:
    """HybridLoop's step-boundary consumption of a REDIRECT."""

    async def test_boundary_replan_adopts_a_plan_without_spending_budget(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """A steering replan revises the plan and is exempt from max_replans."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=2)
        provider = MockCompletionProvider(
            [
                _single_step_plan(),  # initial plan
                _stop_response(),  # step 1 (REDIRECT adopted before it)
                _single_step_plan(),  # steering replan
                _stop_response(),  # revised step 1
            ]
        )
        inbox = _RedirectInbox()
        loop = HybridLoop(config=cfg, steering_inbox=inbox)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # Operator-driven and consume-once, so it never eats into max_replans.
        assert result.metadata["replans_used"] == 0
        assert _plan_count(result.metadata["plans"]) == 2
        assert result.context.pending_steering_replan_id is None
        assert _DIRECTIVE_ID in result.context.adopted_steering_ids

    async def test_boundary_replan_prompt_reaches_the_planner(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """The replan call carries the directive and the planner model."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            checkpoint_after_each_step=False,
            max_replans=2,
            executor_model="example-small-001",
            planner_model="example-large-001",
        )
        provider = MockCompletionProvider(
            [
                _single_step_plan(),
                _stop_response(),
                _single_step_plan(),
                _stop_response(),
            ]
        )
        loop = HybridLoop(config=cfg, steering_inbox=_RedirectInbox())

        await loop.execute(context=ctx, provider=provider)

        # Call 2 is the steering replan: planner model, and the adopted
        # directive must already sit in the conversation it was handed.
        assert provider.recorded_models[2] == "example-large-001"
        replan_messages = provider.recorded_messages[2]
        assert any(
            "switch the storage layer to Postgres" in (m.content or "")
            for m in replan_messages
        )
        assert "revised plan" in (replan_messages[-1].content or "").lower()

    async def test_completion_replan_absorbs_a_pending_directive(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """A completion-triggered replan clears the pending steering flag.

        The revised plan was produced with the directive already in the
        conversation, so a second dedicated steering replan would be
        redundant; the flag must not survive to fire one on a later step.
        """
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            checkpoint_after_each_step=True,
            allow_replan_on_completion=True,
            max_replans=2,
        )
        provider = MockCompletionProvider(
            [
                _multi_step_plan(),  # 3 steps
                _stop_response(),  # step 1 (REDIRECT adopted before it)
                _summary_response(replan=True),  # summary asks for a replan
                _single_step_plan(),  # completion replan -> 1-step plan
                _stop_response(),  # revised step 1
                _summary_response(replan=False),  # summary, last step
            ]
        )
        loop = HybridLoop(config=cfg, steering_inbox=_RedirectInbox())

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # The completion replan counted; no extra steering replan ran.
        assert result.metadata["replans_used"] == 1
        assert _plan_count(result.metadata["plans"]) == 2
        assert result.context.pending_steering_replan_id is None

    async def test_failure_replan_absorbs_a_pending_directive(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """A failure-triggered replan also clears the pending flag."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=2)
        provider = MockCompletionProvider(
            [
                _single_step_plan(),
                _step_fail_response(),  # step 1 fails
                _single_step_plan(),  # failure replan
                _stop_response(),  # revised step 1
            ]
        )
        loop = HybridLoop(config=cfg, steering_inbox=_RedirectInbox())

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1
        assert result.context.pending_steering_replan_id is None


@pytest.mark.unit
class TestHybridMidTurnInterrupt:
    """HybridLoop's re-issue of a streamed turn a REDIRECT interrupted."""

    async def test_interrupted_turn_is_reissued_without_spending_a_turn(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """The aborted turn re-issues and does not consume the turn budget."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=2)
        provider = _StreamingProvider(
            [
                _single_step_plan(),  # planner (non-streaming)
                _single_step_plan(),  # steering replan (non-streaming)
            ]
        )
        # Empty on the first poll so the directive lands mid-stream rather
        # than at the top-of-turn check, which is what forces the interrupt.
        inbox = _RedirectInbox(first_poll_empty=True)
        loop = HybridLoop(config=cfg, steering_inbox=inbox)

        result = await loop.execute(
            context=ctx,
            provider=provider,
            streaming_enabled=True,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        # Three streamed step turns: the interrupted one, its re-issue, and
        # the revised plan's step. The interrupted one records no turn.
        assert provider.stream_calls == 3
        assert result.context.pending_steering_replan_id is None
        assert _DIRECTIVE_ID in result.context.adopted_steering_ids

    async def test_interrupted_turn_folds_its_partial_usage(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """Tokens the aborted stream did surface are kept in the run cost."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(checkpoint_after_each_step=False, max_replans=2)
        provider = _StreamingProvider([_single_step_plan(), _single_step_plan()])
        loop = HybridLoop(
            config=cfg,
            steering_inbox=_RedirectInbox(first_poll_empty=True),
        )

        result = await loop.execute(
            context=ctx,
            provider=provider,
            streaming_enabled=True,
        )

        # The aborted turn records no TurnRecord, so its tokens would vanish
        # if the loop dropped the fold. Run cost must exceed the sum of the
        # recorded turns by exactly the interrupt's partial usage.
        recorded = sum(turn.cost for turn in result.turns)
        assert result.context.accumulated_cost.cost == pytest.approx(
            recorded + _INTERRUPT_USAGE.cost
        )


@pytest.mark.unit
class TestPlanExecuteSteeringReplan:
    """PlanExecuteLoop consumes a REDIRECT at the same boundaries."""

    async def test_boundary_replan_adopts_a_plan_without_spending_budget(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = PlanExecuteConfig(max_replans=2)
        provider = MockCompletionProvider(
            [
                _single_step_plan(),
                _stop_response(),
                _single_step_plan(),
                _stop_response(),
            ]
        )
        loop = PlanExecuteLoop(config=cfg, steering_inbox=_RedirectInbox())

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 0
        assert _plan_count(result.metadata["plans"]) == 2
        assert result.context.pending_steering_replan_id is None

    async def test_failure_replan_absorbs_a_pending_directive(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = PlanExecuteConfig(max_replans=2)
        provider = MockCompletionProvider(
            [
                _single_step_plan(),
                _step_fail_response(),
                _single_step_plan(),
                _stop_response(),
            ]
        )
        loop = PlanExecuteLoop(config=cfg, steering_inbox=_RedirectInbox())

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.metadata["replans_used"] == 1
        assert result.context.pending_steering_replan_id is None

    async def test_interrupted_turn_is_reissued(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = PlanExecuteConfig(max_replans=2)
        provider = _StreamingProvider([_single_step_plan(), _single_step_plan()])
        loop = PlanExecuteLoop(
            config=cfg,
            steering_inbox=_RedirectInbox(first_poll_empty=True),
        )

        result = await loop.execute(
            context=ctx,
            provider=provider,
            streaming_enabled=True,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert provider.stream_calls == 3
        assert result.context.pending_steering_replan_id is None
