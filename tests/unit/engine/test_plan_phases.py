"""Tests for the run-phase layer both plan-based loops share.

``HybridLoop`` and ``PlanExecuteLoop`` inherit one implementation of the
opening planning phase, the terminal classification, and the result
metadata. These pin that they keep inheriting it (a re-added override on
either loop is the regression this guards) and that the two documented
per-loop deltas -- the ``loop_type`` tag and the plan step ceiling -- are
the only things that actually differ.
"""

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.plan_models import ExecutionPlan, PlanStep
from synthorg.engine.plan_phases import PlanPhaseMixin

pytestmark = pytest.mark.unit

_SHARED_METHODS = (
    "get_loop_type",
    "_run_planning_phase",
    "_generate_plan",
    "_build_final_result",
    "_finalize",
)


def _owners(cls: type, name: str) -> list[type]:
    """Classes in *cls*'s MRO that define *name* in their own body.

    Returns:
        Every MRO entry carrying its own definition, nearest first. More
        than one means a subclass has shadowed the shared implementation.
    """
    return [base for base in cls.__mro__ if name in vars(base)]


def _plan(step_count: int) -> ExecutionPlan:
    return ExecutionPlan(
        steps=tuple(
            PlanStep(
                step_number=i + 1,
                description=f"Step {i + 1}",
                expected_outcome="Done",
            )
            for i in range(step_count)
        ),
        original_task_summary="test task",
    )


class TestSharedImplementation:
    """One body per phase method, inherited by both loops."""

    @pytest.mark.parametrize(
        "loop_cls",
        [HybridLoop, PlanExecuteLoop],
        ids=["hybrid", "plan_execute"],
    )
    @pytest.mark.parametrize("name", _SHARED_METHODS)
    def test_only_the_shared_mixin_defines_it(
        self,
        name: str,
        loop_cls: type[HybridLoop] | type[PlanExecuteLoop],
    ) -> None:
        assert _owners(loop_cls, name) == [PlanPhaseMixin]


class TestPlanLimits:
    """The step ceiling is the loop-specific half of plan generation."""

    def test_default_hook_leaves_the_plan_alone(self) -> None:
        # A loop whose configuration carries no ceiling must honour
        # whatever the planner produced rather than inventing a limit.
        plan = _plan(5)
        assert PlanPhaseMixin()._apply_plan_limits(plan) is plan

    def test_plan_execute_inherits_the_default(self) -> None:
        plan = _plan(5)
        assert PlanExecuteLoop()._apply_plan_limits(plan) is plan

    def test_hybrid_truncates_to_its_configured_ceiling(self) -> None:
        loop = HybridLoop(config=HybridLoopConfig(max_plan_steps=2))

        capped = loop._apply_plan_limits(_plan(5))

        assert len(capped.steps) == 2
        assert [s.step_number for s in capped.steps] == [1, 2]

    def test_hybrid_leaves_a_plan_within_the_ceiling_untouched(self) -> None:
        loop = HybridLoop(config=HybridLoopConfig(max_plan_steps=5))
        plan = _plan(2)

        assert loop._apply_plan_limits(plan) is plan


class TestFinalizeMetadata:
    """``_LOOP_TYPE`` is the loop-specific half of the result metadata."""

    @pytest.mark.parametrize(
        ("loop_cls", "expected"),
        [(HybridLoop, "hybrid"), (PlanExecuteLoop, "plan_execute")],
    )
    def test_loop_type_tag_comes_from_the_class(
        self,
        sample_agent_context: AgentContext,
        loop_cls: type[HybridLoop] | type[PlanExecuteLoop],
        expected: str,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
        )

        finalized = loop_cls._finalize(result, [_plan(1)], 0)

        assert finalized.metadata["loop_type"] == expected

    @pytest.mark.parametrize(
        "loop_cls",
        [HybridLoop, PlanExecuteLoop],
        ids=["hybrid", "plan_execute"],
    )
    def test_protocol_accessor_returns_the_same_tag(
        self,
        sample_agent_context: AgentContext,
        loop_cls: type[HybridLoop] | type[PlanExecuteLoop],
    ) -> None:
        # A consumer filtering runs may read either the protocol accessor or
        # the result metadata, so the two must never drift.
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
        )

        finalized = loop_cls._finalize(result, [], 0)

        assert loop_cls().get_loop_type() == finalized.metadata["loop_type"]

    def test_plan_history_and_replan_count_ride_along(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
        )
        first, second = _plan(1), _plan(3)

        finalized = HybridLoop._finalize(result, [first, second], 2)

        plans = finalized.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 2
        assert finalized.metadata["final_plan"] == second.model_dump()
        assert finalized.metadata["replans_used"] == 2

    def test_empty_history_yields_no_final_plan(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.ERROR,
            error_message="planner failed before producing a plan",
        )

        finalized = HybridLoop._finalize(result, [], 0)

        assert finalized.metadata["final_plan"] is None
        assert finalized.metadata["plans"] == []

    def test_caller_metadata_is_preserved(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ExecutionResult(
            context=sample_agent_context,
            termination_reason=TerminationReason.COMPLETED,
            metadata={"carried": "through"},
        )

        finalized = HybridLoop._finalize(result, [], 0)

        assert finalized.metadata["carried"] == "through"
