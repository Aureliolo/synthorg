"""Tests for the parent-rollup status derivation, lifecycle walk, and wrapper."""

from typing import Any, override
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import transition_path
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
)
from synthorg.engine.coordination.parent_rollup import (
    COORDINATOR_ACTOR,
    advance_parent_to_rollup_status,
    compute_status_rollup,
    run_update_parent_phase,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan, DecompositionResult
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.decomposition.status_rollup import SubtaskStatusRollup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import FakeClock, mock_of
from tests.unit.engine.conftest import (
    make_assignment_agent,
    make_assignment_task,
    make_decomposition,
    make_subtask,
)

pytestmark = pytest.mark.unit


class _StaticStrategy:
    """Decomposition strategy returning a fixed plan."""

    def __init__(self, plan: DecompositionPlan) -> None:
        self._plan = plan

    @override
    def __repr__(self) -> str:
        return "_StaticStrategy()"

    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Return the fixed plan.

        Returns:
            The plan supplied at construction.
        """
        return self._plan

    def get_strategy_name(self) -> str:
        """Return the strategy name.

        Returns:
            The literal ``"static"``.
        """
        return "static"

    def plans_any_task(self) -> bool:
        """Answer the recursion question.

        Returns:
            ``True``: it answers with its plan whatever it is asked about.
        """
        return True


def _rollup(
    *,
    total: int,
    completed: int = 0,
    failed: int = 0,
) -> SubtaskStatusRollup:
    """Build a rollup whose ``derived_parent_status`` is predictable."""
    return SubtaskStatusRollup(
        parent_task_id="parent-1",
        total=total,
        completed=completed,
        failed=failed,
        in_progress=0,
        blocked=0,
        cancelled=0,
    )


def _ok_result() -> TaskMutationResult:
    return TaskMutationResult(request_id="r", success=True, version=1)


def _fail_result(error: str) -> TaskMutationResult:
    return TaskMutationResult(
        request_id="r",
        success=False,
        error=error,
        error_code="validation",
        version=1,
    )


class TestAdvanceParentToRollupStatus:
    """Unit coverage for ``advance_parent_to_rollup_status``."""

    async def test_empty_path_is_noop_success(self) -> None:
        """Parent already at the derived status: no submits, success."""
        task_engine = mock_of[TaskEngine](
            submit=AsyncMock(),
            get_task=AsyncMock(return_value=None),
        )
        rollup = _rollup(total=1, completed=1)  # derived COMPLETED

        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id="parent-1",
            current_status=TaskStatus.COMPLETED,
            rollup=rollup,
        )

        assert outcome.success is True
        assert outcome.hops_completed == 0
        assert outcome.error is None
        task_engine.submit.assert_not_awaited()

    async def test_no_valid_path_returns_failure(self) -> None:
        """Terminal parent that cannot reach the derived status."""
        task_engine = mock_of[TaskEngine](
            submit=AsyncMock(),
            get_task=AsyncMock(return_value=None),
        )
        rollup = _rollup(total=1, failed=1)  # derived FAILED

        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id="parent-1",
            current_status=TaskStatus.COMPLETED,
            rollup=rollup,
        )

        assert outcome.success is False
        assert outcome.hops_completed == 0
        assert outcome.error is not None
        assert "no valid lifecycle path" in outcome.error
        task_engine.submit.assert_not_awaited()

    async def test_full_lifecycle_submits_each_valid_hop(self) -> None:
        """Each hop is submitted in lifecycle order with the sentinel."""
        expected = transition_path(TaskStatus.CREATED, TaskStatus.COMPLETED)
        assert expected is not None
        task_engine = mock_of[TaskEngine](
            submit=AsyncMock(return_value=_ok_result()),
            get_task=AsyncMock(return_value=None),
        )
        rollup = _rollup(total=1, completed=1)  # derived COMPLETED

        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id="parent-1",
            current_status=TaskStatus.CREATED,
            rollup=rollup,
        )

        assert outcome.success is True
        assert outcome.hops_completed == len(expected)
        mutations = [call.args[0] for call in task_engine.submit.await_args_list]
        assert [m.target_status for m in mutations] == list(expected)
        assert all(m.requested_by == COORDINATOR_ACTOR for m in mutations)
        for mutation in mutations:
            if mutation.target_status is TaskStatus.ASSIGNED:
                assert mutation.overrides == {"assigned_to": COORDINATOR_ACTOR}
            else:
                assert mutation.overrides == {}

    async def test_partial_hop_failure_records_completed_hops(self) -> None:
        """A mid-walk rejection stops, reports hops + diagnostic note."""
        task_engine = mock_of[TaskEngine](
            submit=AsyncMock(
                side_effect=[
                    _ok_result(),
                    _ok_result(),
                    _fail_result("transition not allowed"),
                ],
            ),
            # First read starts the walk (the parent really is CREATED); the
            # second is the diagnostic re-read after the rejected hop, by
            # which point two hops have landed.
            get_task=AsyncMock(
                side_effect=[
                    make_assignment_task(id="parent-1", status=TaskStatus.CREATED),
                    make_assignment_task(
                        id="parent-1",
                        status=TaskStatus.IN_PROGRESS,
                        assigned_to="coordinator",
                    ),
                ],
            ),
        )
        rollup = _rollup(total=1, completed=1)  # derived COMPLETED

        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id="parent-1",
            current_status=TaskStatus.CREATED,
            rollup=rollup,
        )

        assert outcome.success is False
        assert outcome.hops_completed == 2
        assert outcome.error is not None
        assert "transition not allowed" in outcome.error
        # Diagnostic re-read surfaces the parent's actual live status.
        assert "in_progress" in outcome.error

    async def test_hop_failure_note_falls_back_when_reread_raises(
        self,
    ) -> None:
        """A re-read failure must not mask the original submit error."""
        task_engine = mock_of[TaskEngine](
            submit=AsyncMock(return_value=_fail_result("rejected")),
            get_task=AsyncMock(side_effect=RuntimeError("db down")),
        )
        rollup = _rollup(total=1, completed=1)

        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id="parent-1",
            current_status=TaskStatus.CREATED,
            rollup=rollup,
        )

        assert outcome.success is False
        assert outcome.error is not None
        assert "rejected" in outcome.error
        assert "parent now" not in outcome.error


class TestComputeStatusRollup:
    """The rollup reads persisted status, never the dispatch outcome."""

    def _decomposition(self) -> DecompositionResult:
        subtasks = (make_subtask("sub-a"), make_subtask("sub-b"))
        return make_decomposition(subtasks, parent_task_id="parent-1")

    async def test_reads_persisted_status_not_run_outcome(self) -> None:
        """A finished-but-unverified subtask counts as IN_REVIEW, not COMPLETED.

        A run that returned successfully is not a run the review gate has
        passed, so deriving the parent from the dispatch outcome would let it
        complete on unverified work.
        """
        decomp = self._decomposition()
        live = {
            s.id: make_assignment_task(
                id=s.id, status=TaskStatus.IN_REVIEW, assigned_to="alice"
            )
            for s in decomp.plan.subtasks
        }
        phases: list[CoordinationPhaseResult] = []

        rollup = await compute_status_rollup(
            decomposition_service=DecompositionService(
                _StaticStrategy(decomp.plan), TaskStructureClassifier()
            ),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=lambda tid: live[tid]),
            ),
            clock=FakeClock(),
            context=CoordinationContext(
                task=make_assignment_task(id="parent-1"),
                available_agents=(make_assignment_agent("alice"),),
            ),
            decomp_result=decomp,
            phases=phases,
        )

        assert rollup is not None
        assert rollup.completed == 0
        assert rollup.derived_parent_status is TaskStatus.IN_PROGRESS
        assert phases[-1].success is True

    async def test_missing_subtask_row_counts_as_blocked(self) -> None:
        """A subtask that never reached the engine holds the total honest."""
        decomp = self._decomposition()
        phases: list[CoordinationPhaseResult] = []

        rollup = await compute_status_rollup(
            decomposition_service=DecompositionService(
                _StaticStrategy(decomp.plan), TaskStructureClassifier()
            ),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=None),
            ),
            clock=FakeClock(),
            context=CoordinationContext(
                task=make_assignment_task(id="parent-1"),
                available_agents=(make_assignment_agent("alice"),),
            ),
            decomp_result=decomp,
            phases=phases,
        )

        assert rollup is not None
        assert rollup.blocked == len(decomp.plan.subtasks)

    async def test_verified_subtasks_complete_the_parent(self) -> None:
        """Once every child has passed the gate, the parent derives COMPLETED."""
        decomp = self._decomposition()
        live = {
            s.id: make_assignment_task(
                id=s.id, status=TaskStatus.COMPLETED, assigned_to="alice"
            )
            for s in decomp.plan.subtasks
        }
        phases: list[CoordinationPhaseResult] = []

        rollup = await compute_status_rollup(
            decomposition_service=DecompositionService(
                _StaticStrategy(decomp.plan), TaskStructureClassifier()
            ),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=lambda tid: live[tid]),
            ),
            clock=FakeClock(),
            context=CoordinationContext(
                task=make_assignment_task(id="parent-1"),
                available_agents=(make_assignment_agent("alice"),),
            ),
            decomp_result=decomp,
            phases=phases,
        )

        assert rollup is not None
        assert rollup.derived_parent_status is TaskStatus.COMPLETED

    async def test_no_task_engine_returns_none(self) -> None:
        """No engine means no persisted status, so no rollup is invented."""
        decomp = self._decomposition()
        phases: list[CoordinationPhaseResult] = []

        rollup = await compute_status_rollup(
            decomposition_service=DecompositionService(
                _StaticStrategy(decomp.plan), TaskStructureClassifier()
            ),
            task_engine=None,
            clock=FakeClock(),
            context=CoordinationContext(
                task=make_assignment_task(id="parent-1"),
                available_agents=(make_assignment_agent("alice"),),
            ),
            decomp_result=decomp,
            phases=phases,
        )

        assert rollup is None


class TestRunUpdateParentPhase:
    """Unit coverage for the ``run_update_parent_phase`` wrapper."""

    def _context(self) -> CoordinationContext:
        return CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

    async def test_no_task_engine_is_noop(self) -> None:
        """No task engine wired: nothing recorded."""
        phases: list[CoordinationPhaseResult] = []
        await run_update_parent_phase(
            task_engine=None,
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )
        assert phases == []

    async def test_rollup_none_records_failed_phase(self) -> None:
        """Missing rollup records a failed update_parent phase."""
        phases: list[CoordinationPhaseResult] = []
        await run_update_parent_phase(
            task_engine=mock_of[TaskEngine](),
            clock=FakeClock(),
            context=self._context(),
            rollup=None,
            phases=phases,
        )
        assert len(phases) == 1
        assert phases[0].phase == "update_parent"
        assert phases[0].success is False
        assert phases[0].error is not None
        assert "rollup is None" in phases[0].error

    async def test_parent_not_found_records_failed_phase(self) -> None:
        """A missing live parent records a failed phase, no raise."""
        phases: list[CoordinationPhaseResult] = []
        await run_update_parent_phase(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=None),
            ),
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )
        assert len(phases) == 1
        assert phases[0].success is False
        assert phases[0].error is not None
        assert "not found" in phases[0].error

    async def test_get_task_exception_is_captured_not_raised(self) -> None:
        """A TaskEngine exception is captured as a failed phase."""
        phases: list[CoordinationPhaseResult] = []
        await run_update_parent_phase(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=RuntimeError("engine down")),
            ),
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )
        assert len(phases) == 1
        assert phases[0].success is False
        assert phases[0].error is not None
        assert "engine down" in phases[0].error

    async def test_happy_path_records_success_with_hops(self) -> None:
        """A successful walk records a successful phase."""
        expected = transition_path(TaskStatus.CREATED, TaskStatus.COMPLETED)
        assert expected is not None
        phases: list[CoordinationPhaseResult] = []
        await run_update_parent_phase(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(
                    return_value=make_assignment_task(id="parent-1"),
                ),
                submit=AsyncMock(return_value=_ok_result()),
            ),
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )
        assert len(phases) == 1
        assert phases[0].success is True
        assert phases[0].error is None


class TestOnlyOneWriterWalksTheParent:
    """A plan-driven parent belongs to the initiative rollup, not this walk.

    Two rollups ran against one objective task 25ms apart, derived
    ``0/7 completed`` and ``1/8 completed`` from different populations, and
    the second walked the task back out of the terminal status the first had
    just set. The counts are both honest; having two of them is the defect.
    """

    def _engine(self, task: Task) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any so the call asserts stay reachable
        return mock_of[TaskEngine](
            get_task=AsyncMock(return_value=task),
            submit=AsyncMock(return_value=_ok_result()),
        )

    async def test_a_run_a_plan_provisioned_does_not_walk_the_parent(self) -> None:
        # The live shape: an objective task carries no `plan_id` of its own
        # (the link lives on `Plan.parent_task_id`), so only the run's own
        # context can say who owns it.
        engine = self._engine(make_assignment_task(id="parent-1"))
        phases: list[CoordinationPhaseResult] = []

        await run_update_parent_phase(
            task_engine=engine,
            clock=FakeClock(),
            context=self._plan_context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )

        engine.submit.assert_not_awaited()
        assert [(p.phase, p.success) for p in phases] == [("update_parent", True)]

    async def test_a_plan_item_parent_is_walked_because_nothing_else_walks_it(
        self,
    ) -> None:
        """Deferring on the column alone leaves this parent with NO writer.

        ``plan_mapping`` stamps ``plan_id`` on every child task a plan
        creates, so a plan-item task matches the column. But the initiative
        rollup walks ``Plan.parent_task_id`` and nothing else, so it never
        visits a plan-item task that is itself a coordination parent. Reading
        the column as evidence of an owner therefore hands it to a walk that
        does not happen, and it sits IN_PROGRESS for ever while its plan can
        never conclude. Only the run's own context can say who owns this.
        """
        engine = self._engine(
            make_assignment_task(id="parent-1").model_copy(update={"plan_id": "plan-7"})
        )
        phases: list[CoordinationPhaseResult] = []

        await run_update_parent_phase(
            task_engine=engine,
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )

        engine.submit.assert_awaited()

    async def test_a_run_no_plan_provisioned_still_walks(self) -> None:
        """The other rung of the ladder: with no plan, this walk is the writer."""
        engine = self._engine(make_assignment_task(id="parent-1"))
        phases: list[CoordinationPhaseResult] = []

        await run_update_parent_phase(
            task_engine=engine,
            clock=FakeClock(),
            context=self._context(),
            rollup=_rollup(total=1, completed=1),
            phases=phases,
        )

        engine.submit.assert_awaited()

    def _context(self) -> CoordinationContext:
        return CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

    def _plan_context(self) -> CoordinationContext:
        return CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
            plan_id=NotBlankStr("plan-7"),
        )
