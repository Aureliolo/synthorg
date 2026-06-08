"""Tests for the parent-rollup lifecycle walk and phase wrapper."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import transition_path
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
)
from synthorg.engine.coordination.parent_rollup import (
    COORDINATOR_ACTOR,
    advance_parent_to_rollup_status,
    run_update_parent_phase,
)
from synthorg.engine.decomposition.models import SubtaskStatusRollup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import FakeClock, mock_of
from tests.unit.engine.conftest import (
    make_assignment_agent,
    make_assignment_task,
)

pytestmark = pytest.mark.unit


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
        task_engine = mock_of[TaskEngine](submit=AsyncMock())
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
        task_engine = mock_of[TaskEngine](submit=AsyncMock())
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
            get_task=AsyncMock(
                return_value=make_assignment_task(
                    id="parent-1",
                    status=TaskStatus.IN_PROGRESS,
                    assigned_to="coordinator",
                ),
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
