"""Tests for coordination domain models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import CoordinationTopology, TaskStatus
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
    CoordinationResult,
    CoordinationWave,
)
from synthorg.engine.decomposition.models import SubtaskStatusRollup
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from tests.unit.engine.conftest import make_assignment_agent, make_assignment_task


class TestCoordinationContext:
    """CoordinationContext model tests."""

    @pytest.mark.unit
    def test_valid_context(self) -> None:
        """Context with task and agents is valid."""
        task = make_assignment_task()
        agent = make_assignment_agent("alice")
        ctx = CoordinationContext(
            task=task,
            available_agents=(agent,),
        )
        assert ctx.task.id == task.id
        assert len(ctx.available_agents) == 1

    @pytest.mark.unit
    def test_default_decomposition_context(self) -> None:
        """Default decomposition context is created."""
        ctx = CoordinationContext(
            task=make_assignment_task(),
            available_agents=(make_assignment_agent("alice"),),
        )
        assert ctx.decomposition_context.max_subtasks == 10
        assert ctx.decomposition_context.max_depth == 3

    @pytest.mark.unit
    def test_default_config(self) -> None:
        """Default coordination config is created."""
        ctx = CoordinationContext(
            task=make_assignment_task(),
            available_agents=(make_assignment_agent("alice"),),
        )
        assert ctx.config.fail_fast is False

    @pytest.mark.unit
    def test_empty_agents_rejected(self) -> None:
        """Empty agents tuple is rejected."""
        with pytest.raises(
            ValidationError,
            match="available_agents must contain at least one agent",
        ):
            CoordinationContext(
                task=make_assignment_task(),
                available_agents=(),
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Context is immutable."""
        ctx = CoordinationContext(
            task=make_assignment_task(),
            available_agents=(make_assignment_agent("alice"),),
        )
        with pytest.raises(ValidationError):
            ctx.task = make_assignment_task(id="other")  # type: ignore[misc]

    @pytest.mark.unit
    def test_custom_config(self) -> None:
        """Custom config is preserved."""
        cfg = CoordinationConfig(fail_fast=True, max_concurrency_per_wave=2)
        ctx = CoordinationContext(
            task=make_assignment_task(),
            available_agents=(make_assignment_agent("alice"),),
            config=cfg,
        )
        assert ctx.config.fail_fast is True
        assert ctx.config.max_concurrency_per_wave == 2


class TestCoordinationPhaseResult:
    """CoordinationPhaseResult model tests."""

    @pytest.mark.unit
    def test_success_phase(self) -> None:
        """Successful phase has no error."""
        phase = CoordinationPhaseResult(
            phase="decompose",
            success=True,
            duration_seconds=1.5,
        )
        assert phase.success is True
        assert phase.error is None
        assert phase.duration_seconds == 1.5

    @pytest.mark.unit
    def test_failed_phase(self) -> None:
        """Failed phase carries error message."""
        phase = CoordinationPhaseResult(
            phase="route",
            success=False,
            duration_seconds=0.1,
            error="No eligible agents",
        )
        assert phase.success is False
        assert phase.error == "No eligible agents"

    @pytest.mark.unit
    def test_negative_duration_rejected(self) -> None:
        """Negative duration is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CoordinationPhaseResult(
                phase="decompose",
                success=True,
                duration_seconds=-1.0,
            )

    @pytest.mark.unit
    def test_blank_phase_rejected(self) -> None:
        """Blank phase name is rejected."""
        with pytest.raises(ValidationError):
            CoordinationPhaseResult(
                phase="   ",
                success=True,
                duration_seconds=0.0,
            )

    @pytest.mark.unit
    def test_success_with_error_rejected(self) -> None:
        """Successful phase with error is rejected."""
        with pytest.raises(
            ValidationError,
            match="successful phase must not have an error",
        ):
            CoordinationPhaseResult(
                phase="decompose",
                success=True,
                duration_seconds=1.0,
                error="something",
            )

    @pytest.mark.unit
    def test_failed_without_error_rejected(self) -> None:
        """Failed phase without error description is rejected."""
        with pytest.raises(
            ValidationError,
            match="failed phase must have an error description",
        ):
            CoordinationPhaseResult(
                phase="decompose",
                success=False,
                duration_seconds=1.0,
            )


class TestCoordinationWave:
    """CoordinationWave model tests."""

    @pytest.mark.unit
    def test_wave_without_result(self) -> None:
        """Wave can be created without execution result."""
        wave = CoordinationWave(
            wave_index=0,
            subtask_ids=("sub-1", "sub-2"),
        )
        assert wave.wave_index == 0
        assert wave.subtask_ids == ("sub-1", "sub-2")
        assert wave.execution_result is None

    @pytest.mark.unit
    def test_wave_with_result(self) -> None:
        """Wave can carry a parallel execution result."""
        exec_result = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                AgentOutcome(
                    task_id="sub-1",
                    agent_id="agent-1",
                    error="test error",
                ),
            ),
            total_duration_seconds=2.0,
        )
        wave = CoordinationWave(
            wave_index=0,
            subtask_ids=("sub-1",),
            execution_result=exec_result,
        )
        assert wave.execution_result is not None
        assert wave.execution_result.group_id == "wave-0"

    @pytest.mark.unit
    def test_negative_wave_index_rejected(self) -> None:
        """Negative wave index is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CoordinationWave(
                wave_index=-1,
                subtask_ids=("sub-1",),
            )

    @pytest.mark.unit
    def test_empty_subtask_ids_rejected(self) -> None:
        """Empty subtask_ids is rejected."""
        with pytest.raises(
            ValidationError,
            match="subtask_ids must contain at least one ID",
        ):
            CoordinationWave(
                wave_index=0,
                subtask_ids=(),
            )


class TestCoordinationResult:
    """CoordinationResult model tests."""

    @pytest.mark.unit
    def test_all_phases_succeed(self) -> None:
        """is_success is True when all phases succeed."""
        result = CoordinationResult(
            parent_task_id="task-1",
            topology=CoordinationTopology.CENTRALIZED,
            phases=(
                CoordinationPhaseResult(
                    phase="decompose", success=True, duration_seconds=1.0
                ),
                CoordinationPhaseResult(
                    phase="route", success=True, duration_seconds=0.5
                ),
            ),
            total_duration_seconds=1.5,
        )
        assert result.is_success is True

    @pytest.mark.unit
    def test_any_phase_fails(self) -> None:
        """is_success is False when any phase fails."""
        result = CoordinationResult(
            parent_task_id="task-1",
            topology=CoordinationTopology.SAS,
            phases=(
                CoordinationPhaseResult(
                    phase="decompose", success=True, duration_seconds=1.0
                ),
                CoordinationPhaseResult(
                    phase="route",
                    success=False,
                    duration_seconds=0.1,
                    error="failed",
                ),
            ),
            total_duration_seconds=1.1,
        )
        assert result.is_success is False

    @pytest.mark.unit
    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        result = CoordinationResult(
            parent_task_id="task-1",
            topology=CoordinationTopology.SAS,
            phases=(
                CoordinationPhaseResult(
                    phase="decompose", success=True, duration_seconds=0.0
                ),
            ),
            total_duration_seconds=0.0,
        )
        assert result.decomposition_result is None
        assert result.routing_result is None
        assert result.status_rollup is None
        assert result.workspace_merge is None
        assert result.waves == ()
        assert result.total_cost_usd == 0.0

    @pytest.mark.unit
    def test_with_status_rollup(self) -> None:
        """Result can carry a status rollup."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=2,
            completed=2,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        result = CoordinationResult(
            parent_task_id="task-1",
            topology=CoordinationTopology.CENTRALIZED,
            phases=(
                CoordinationPhaseResult(
                    phase="rollup", success=True, duration_seconds=0.01
                ),
            ),
            status_rollup=rollup,
            total_duration_seconds=5.0,
        )
        assert result.status_rollup is not None
        assert result.status_rollup.derived_parent_status == TaskStatus.COMPLETED

    @pytest.mark.unit
    def test_negative_duration_rejected(self) -> None:
        """Negative total_duration_seconds is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CoordinationResult(
                parent_task_id="task-1",
                topology=CoordinationTopology.SAS,
                phases=(
                    CoordinationPhaseResult(
                        phase="test", success=True, duration_seconds=0.0
                    ),
                ),
                total_duration_seconds=-1.0,
            )

    @pytest.mark.unit
    def test_negative_cost_rejected(self) -> None:
        """Negative total_cost_usd is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CoordinationResult(
                parent_task_id="task-1",
                topology=CoordinationTopology.SAS,
                phases=(
                    CoordinationPhaseResult(
                        phase="test", success=True, duration_seconds=0.0
                    ),
                ),
                total_duration_seconds=0.0,
                total_cost_usd=-1.0,
            )

    @pytest.mark.unit
    def test_empty_phases_rejected(self) -> None:
        """Empty phases tuple is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            CoordinationResult(
                parent_task_id="task-1",
                topology=CoordinationTopology.SAS,
                phases=(),
                total_duration_seconds=0.0,
            )

    @pytest.mark.unit
    def test_auto_topology_rejected(self) -> None:
        """AUTO topology is rejected in CoordinationResult."""
        with pytest.raises(
            ValidationError,
            match="resolved, not AUTO",
        ):
            CoordinationResult(
                parent_task_id="task-1",
                topology=CoordinationTopology.AUTO,
                phases=(
                    CoordinationPhaseResult(
                        phase="decompose",
                        success=True,
                        duration_seconds=0.0,
                    ),
                ),
                total_duration_seconds=0.0,
            )

    @pytest.mark.unit
    def test_mismatched_parent_task_id_accepted(self) -> None:
        """Mismatched parent_task_id in result vs rollup is accepted.

        Documents current behavior: CoordinationResult does not
        cross-validate parent_task_id against nested models.
        """
        rollup = SubtaskStatusRollup(
            parent_task_id="other-task",
            total=1,
            completed=1,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        result = CoordinationResult(
            parent_task_id="task-1",
            topology=CoordinationTopology.CENTRALIZED,
            phases=(
                CoordinationPhaseResult(
                    phase="rollup",
                    success=True,
                    duration_seconds=0.01,
                ),
            ),
            status_rollup=rollup,
            total_duration_seconds=1.0,
        )
        # Mismatched IDs are accepted (no cross-validation)
        assert result.parent_task_id == "task-1"
        assert result.status_rollup is not None
        assert result.status_rollup.parent_task_id == "other-task"
