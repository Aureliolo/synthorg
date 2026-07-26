"""Tests for the shared plan-loop run objects.

These objects exist to stop ``HybridLoop`` and ``PlanExecuteLoop`` from
hand-threading a fifteen-value bundle through every step-execution and
replan helper.  The invariants below are what make that safe: the
collaborator bundle cannot be built positionally (so two same-typed model
ids cannot be transposed), it cannot be mutated after construction, and
the mutable cursor evolves ``AgentContext`` values without mutating them.
"""

import dataclasses

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.plan_loop_context import (
    StepRunContext,
    StepRunState,
    StepTurnOutcome,
)
from synthorg.engine.plan_models import ExecutionPlan, PlanStep
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

from .conftest import MockCompletionProvider

pytestmark = pytest.mark.unit


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=(
            PlanStep(
                step_number=1,
                description="Research the topic",
                expected_outcome="Understanding gained",
            ),
        ),
        original_task_summary="test task",
    )


def _run(provider: MockCompletionProvider) -> StepRunContext:
    return StepRunContext(
        provider=provider,
        executor_model="example-small-001",
        planner_model="example-large-001",
        completion_config=CompletionConfig(),
    )


class TestStepRunContext:
    """Construction-time guarantees for the collaborator bundle."""

    def test_rejects_positional_construction(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        with pytest.raises(TypeError):
            StepRunContext(  # type: ignore[misc]
                provider,
                "example-small-001",
                "example-large-001",
                CompletionConfig(),
            )

    def test_keeps_the_two_model_ids_distinct(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        run = _run(mock_provider_factory([]))
        assert run.executor_model == "example-small-001"
        assert run.planner_model == "example-large-001"

    def test_is_frozen(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        run = _run(mock_provider_factory([]))
        with pytest.raises(dataclasses.FrozenInstanceError):
            run.executor_model = "example-medium-001"  # type: ignore[misc]

    def test_optional_collaborators_default_to_none(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        run = _run(mock_provider_factory([]))
        assert run.tool_invoker is None
        assert run.budget_checker is None
        assert run.shutdown_checker is None
        assert run.task_cancellation_checker is None
        assert run.turn_observer is None
        assert run.checkpoint_callback is None
        assert run.streaming_enabled is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_executor_model(
        self,
        mock_provider_factory: type[MockCompletionProvider],
        blank: str,
    ) -> None:
        with pytest.raises(ValueError, match="executor_model"):
            StepRunContext(
                provider=mock_provider_factory([]),
                executor_model=blank,
                planner_model="example-large-001",
                completion_config=CompletionConfig(),
            )

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_planner_model(
        self,
        mock_provider_factory: type[MockCompletionProvider],
        blank: str,
    ) -> None:
        with pytest.raises(ValueError, match="planner_model"):
            StepRunContext(
                provider=mock_provider_factory([]),
                executor_model="example-small-001",
                planner_model=blank,
                completion_config=CompletionConfig(),
            )


class TestStepRunState:
    """Cursor and accumulator semantics for the mutable half."""

    def test_rejects_positional_construction(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        with pytest.raises(TypeError):
            StepRunState(  # type: ignore[misc]
                sample_agent_context,
                _plan(),
                [],
                [],
            )

    def test_cursor_starts_at_the_first_step(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = StepRunState(
            ctx=sample_agent_context,
            plan=_plan(),
            turns=[],
            all_plans=[],
        )
        assert state.step_idx == 0
        assert state.replans_used == 0

    def test_rebinding_ctx_leaves_the_previous_value_untouched(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = StepRunState(
            ctx=sample_agent_context,
            plan=_plan(),
            turns=[],
            all_plans=[],
        )
        before = state.ctx
        message_count = len(before.conversation)

        state.ctx = state.ctx.with_message(
            ChatMessage(role=MessageRole.USER, content="Do something")
        )

        assert state.ctx is not before
        assert len(before.conversation) == message_count
        assert len(state.ctx.conversation) == message_count + 1

    def test_accumulators_keep_the_caller_s_list_identity(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        turns: list[TurnRecord] = []
        all_plans: list[ExecutionPlan] = []
        state = StepRunState(
            ctx=sample_agent_context,
            plan=_plan(),
            turns=turns,
            all_plans=all_plans,
        )

        state.all_plans.append(_plan())

        assert state.turns is turns
        assert state.all_plans is all_plans
        assert len(all_plans) == 1


class TestStepTurnOutcome:
    """The step-turn sentinel must not collide with the boolean arm."""

    def test_is_a_single_member_enum(self) -> None:
        # A second member would silently widen the step-turn contract without
        # forcing the three-way dispatch below to grow an arm.
        assert list(StepTurnOutcome) == [StepTurnOutcome.CONTINUE]

    def test_continue_is_truthy_so_falsy_checks_cannot_confuse_it(self) -> None:
        # A ``if not outcome:`` check must not treat CONTINUE like a failed
        # step, which is exactly the bug a bare ``None`` sentinel would invite.
        assert bool(StepTurnOutcome.CONTINUE)

    def test_dispatch_arms_are_mutually_exclusive(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
        )
        arms: list[ExecutionResult | StepTurnOutcome | bool] = [
            result,
            StepTurnOutcome.CONTINUE,
            True,
            False,
        ]
        classified = [
            "terminate"
            if isinstance(arm, ExecutionResult)
            else "continue"
            if arm is StepTurnOutcome.CONTINUE
            else "step_done"
            for arm in arms
        ]
        assert classified == ["terminate", "continue", "step_done", "step_done"]
