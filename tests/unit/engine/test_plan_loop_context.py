"""Tests for the shared plan-loop run objects.

The invariants below are what let ``HybridLoop`` and ``PlanExecuteLoop``
share one bundle safely: the collaborator half cannot be built positionally
(so two same-typed model ids cannot be transposed), it cannot be mutated
after construction, it keeps credential-bearing collaborators out of its
repr, and the mutable half evolves ``AgentContext`` values without mutating
them while keeping its replan bookkeeping in step.
"""

import dataclasses

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.plan_loop_context import (
    ReplanTrigger,
    ReplanVerdict,
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


def _plan(summary: str = "test task") -> ExecutionPlan:
    return ExecutionPlan(
        steps=(
            PlanStep(
                step_number=1,
                description="Research the topic",
                expected_outcome="Understanding gained",
            ),
        ),
        original_task_summary=summary,
    )


def _run(provider: MockCompletionProvider) -> StepRunContext:
    return StepRunContext(
        provider=provider,
        executor_model="example-small-001",
        planner_model="example-large-001",
        completion_config=CompletionConfig(),
    )


def _state(ctx: AgentContext) -> StepRunState:
    return StepRunState(ctx=ctx, plan=_plan(), turns=[], all_plans=[])


class TestStepRunContext:
    """Construction-time guarantees for the collaborator bundle."""

    def test_rejects_positional_construction(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        with pytest.raises(TypeError):
            StepRunContext(  # type: ignore[call-arg]
                provider,
                "example-small-001",
                "example-large-001",
                CompletionConfig(),
            )

    def test_is_frozen(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        run = _run(mock_provider_factory([]))
        with pytest.raises(dataclasses.FrozenInstanceError):
            run.executor_model = "example-medium-001"  # type: ignore[misc]

    def test_repr_omits_credential_bearing_collaborators(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        # A driver caches resolved credentials, so an incidental repr (an APM
        # frame-locals capture, a verbose assertion message) must not reach
        # into the provider at all.
        run = _run(mock_provider_factory([]))
        rendered = repr(run)
        assert "provider=" not in rendered
        assert "tool_invoker=" not in rendered
        assert "example-small-001" in rendered

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
            StepRunState(  # type: ignore[call-arg]
                sample_agent_context,
                _plan(),
                [],
                [],
            )

    def test_requires_explicit_accumulators(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        # No default: a state built without them would hold accumulators
        # disconnected from the ones the planning phase already appended to,
        # which is the split the two-object design exists to prevent.
        with pytest.raises(TypeError):
            StepRunState(  # type: ignore[call-arg]
                ctx=sample_agent_context,
                plan=_plan(),
            )

    def test_cursor_starts_at_the_first_step(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
        assert state.step_idx == 0
        assert state.replans_used == 0

    def test_rebinding_ctx_leaves_the_previous_value_untouched(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
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

    def test_advance_and_restart_move_the_cursor(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
        state.advance_step()
        state.advance_step()
        assert state.step_idx == 2
        state.restart_plan()
        assert state.step_idx == 0

    def test_record_replan_adopts_counts_and_appends_together(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
        revised = _plan("revised task")

        state.record_replan(revised)

        assert state.plan is revised
        assert state.all_plans == [revised]
        assert state.replans_used == 1

    def test_record_replan_can_skip_the_budget(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        # An operator steering directive is consume-once and must not eat
        # into max_replans, but it still joins the plan history.
        state = _state(sample_agent_context)
        revised = _plan("steered task")

        state.record_replan(revised, counts_against_budget=False)

        assert state.plan is revised
        assert state.all_plans == [revised]
        assert state.replans_used == 0

    def test_sync_current_plan_replaces_the_last_history_entry(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
        state.record_replan(_plan("first revision"))
        live = _plan("live statuses")
        state.plan = live

        state.sync_current_plan()

        assert state.all_plans == [live]

    def test_sync_current_plan_is_a_no_op_on_an_empty_history(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        state = _state(sample_agent_context)
        state.sync_current_plan()
        assert state.all_plans == []


class TestStepTurnOutcome:
    """Every step-turn arm is nominal, so none shares the falsy bucket."""

    def test_every_member_is_truthy(self) -> None:
        # An ``if not outcome:`` check must not read any arm as absence,
        # which is exactly the bug a ``None`` / ``False`` arm would invite.
        assert all(bool(member) for member in StepTurnOutcome)

    def test_is_a_plain_enum_not_an_int_or_str_enum(self) -> None:
        # An IntEnum member satisfies isinstance(x, int) and compares equal
        # to a bool, which would collapse the very dispatch this enum exists
        # to keep apart from the ExecutionResult arm.
        assert not issubclass(StepTurnOutcome, int)
        assert not issubclass(StepTurnOutcome, str)

    @pytest.mark.parametrize(
        ("success", "expected"),
        [
            (True, StepTurnOutcome.STEP_SUCCEEDED),
            (False, StepTurnOutcome.STEP_FAILED),
        ],
    )
    def test_from_success_maps_the_flag(
        self,
        success: bool,
        expected: StepTurnOutcome,
    ) -> None:
        assert StepTurnOutcome.from_success(success=success) is expected

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (StepTurnOutcome.STEP_SUCCEEDED, True),
            (StepTurnOutcome.STEP_FAILED, False),
            (StepTurnOutcome.CONTINUE, False),
        ],
    )
    def test_step_succeeded_is_true_only_for_the_success_arm(
        self,
        member: StepTurnOutcome,
        expected: bool,
    ) -> None:
        assert member.step_succeeded is expected


class TestReplanVerdict:
    """The replan decision is nominal for the same reason."""

    def test_every_member_is_truthy(self) -> None:
        assert all(bool(member) for member in ReplanVerdict)

    @pytest.mark.parametrize(
        ("replan", "expected"),
        [
            (True, ReplanVerdict.REPLAN),
            (False, ReplanVerdict.PROCEED),
        ],
    )
    def test_from_flag_maps_the_decision(
        self,
        replan: bool,
        expected: ReplanVerdict,
    ) -> None:
        assert ReplanVerdict.from_flag(replan=replan) is expected

    def test_wants_replan_is_true_only_for_replan(self) -> None:
        assert ReplanVerdict.REPLAN.wants_replan is True
        assert ReplanVerdict.PROCEED.wants_replan is False


class TestReplanTrigger:
    """One stable value set behind every replan-start log line."""

    def test_step_failed_is_true_only_for_the_failure_trigger(self) -> None:
        assert ReplanTrigger.STEP_FAILURE.step_failed is True
        assert ReplanTrigger.COMPLETION_SUMMARY.step_failed is False
        assert ReplanTrigger.STEERING.step_failed is False

    def test_values_are_stable_log_tokens(self) -> None:
        # These strings reach the log stream, so a rename is a consumer break.
        assert {t.value for t in ReplanTrigger} == {
            "step_failure",
            "completion_summary",
            "steering",
        }
