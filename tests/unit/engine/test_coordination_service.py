"""Tests for MultiAgentCoordinator service."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from synthorg.engine.decomposition.models import DecompositionResult

from synthorg.core.enums import (
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
)
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.models import (
    CoordinationContext,
)
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from synthorg.engine.routing.models import (
    RoutingResult,
)
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.engine.workspace.models import (
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
)
from tests.unit.engine.conftest import (
    build_run_result,
    make_assignment_agent,
    make_assignment_task,
    make_decomposition,
    make_exec_result,
    make_routing,
    make_subtask,
)

# ── Helpers ─────────────────────────────────────────────────────


def _make_coordinator(  # noqa: PLR0913
    *,
    decomp_result: DecompositionResult | None = None,
    routing_result: RoutingResult | None = None,
    exec_results: list[ParallelExecutionResult] | None = None,
    workspace_service: AsyncMock | None = None,
    task_engine: AsyncMock | None = None,
    decompose_error: Exception | None = None,
    route_error: Exception | None = None,
) -> MultiAgentCoordinator:
    """Build a MultiAgentCoordinator with mocked dependencies."""
    decomp_service = AsyncMock()
    if decompose_error:
        decomp_service.decompose_task.side_effect = decompose_error
    elif decomp_result:
        decomp_service.decompose_task.return_value = decomp_result
    decomp_service.rollup_status = MagicMock()
    if decomp_result:
        from synthorg.engine.decomposition.rollup import StatusRollup

        decomp_service.rollup_status.side_effect = StatusRollup.compute

    routing_service = MagicMock()
    if route_error:
        routing_service.route.side_effect = route_error
    elif routing_result:
        routing_service.route.return_value = routing_result

    executor = AsyncMock()
    if exec_results:
        executor.execute_group.side_effect = exec_results

    return MultiAgentCoordinator(
        decomposition_service=decomp_service,
        routing_service=routing_service,
        parallel_executor=executor,
        workspace_service=workspace_service,
        task_engine=task_engine,
    )


# ── Tests ───────────────────────────────────────────────────────


class TestMultiAgentCoordinator:
    """MultiAgentCoordinator tests."""

    @pytest.mark.unit
    async def test_happy_path_two_parallel_subtasks(self) -> None:
        """Full pipeline with 2 parallel subtasks succeeds."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "bob"),
            ]
        )

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result(
                    "wave-0",
                    [
                        ("sub-a", agent_id_a),
                        ("sub-b", agent_id_b),
                    ],
                ),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )

        result = await coordinator.coordinate(ctx)

        assert result.is_success
        assert result.topology == CoordinationTopology.CENTRALIZED
        assert result.decomposition_result is not None
        assert result.routing_result is not None
        assert len(result.waves) == 1
        assert result.status_rollup is not None
        assert result.status_rollup.completed == 2
        assert result.total_duration_seconds > 0

    @pytest.mark.unit
    async def test_sas_topology_single_agent(self) -> None:
        """SAS topology with sequential subtasks."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            topology=CoordinationTopology.SAS,
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [("sub-a", "alice"), ("sub-b", "alice")],
            topology=CoordinationTopology.SAS,
        )

        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
                make_exec_result("wave-1", [("sub-b", agent_id)]),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)

        assert result.is_success
        assert result.topology == CoordinationTopology.SAS
        assert len(result.waves) == 2

    @pytest.mark.unit
    async def test_decompose_failure_raises_phase_error(self) -> None:
        """Decompose failure raises CoordinationPhaseError."""
        coordinator = _make_coordinator(
            decompose_error=RuntimeError("LLM down"),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        with pytest.raises(CoordinationPhaseError) as exc_info:
            await coordinator.coordinate(ctx)

        assert exc_info.value.phase == "decompose"
        assert len(exc_info.value.partial_phases) > 0

    @pytest.mark.unit
    async def test_route_failure_raises_phase_error(self) -> None:
        """Route failure raises CoordinationPhaseError."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))

        coordinator = _make_coordinator(
            decomp_result=decomp,
            route_error=RuntimeError("Routing broken"),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        with pytest.raises(CoordinationPhaseError) as exc_info:
            await coordinator.coordinate(ctx)

        assert exc_info.value.phase == "route"
        # Should have decompose phase in partial_phases
        assert len(exc_info.value.partial_phases) >= 2

    @pytest.mark.unit
    async def test_all_unroutable_raises_phase_error(self) -> None:
        """All unroutable subtasks raises CoordinationPhaseError."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = RoutingResult(
            parent_task_id="parent-1",
            unroutable=("sub-a",),
        )

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        with pytest.raises(CoordinationPhaseError) as exc_info:
            await coordinator.coordinate(ctx)

        assert exc_info.value.phase == "validate"

    @pytest.mark.unit
    async def test_partial_execution_fail_fast_off(self) -> None:
        """With fail_fast=False, failed waves don't stop execution."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "bob"),
            ]
        )

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                # Wave 0 fails
                make_exec_result("wave-0", [("sub-a", agent_id_a)], all_succeed=False),
                # Wave 1 succeeds
                make_exec_result("wave-1", [("sub-b", agent_id_b)], all_succeed=True),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
            config=CoordinationConfig(fail_fast=False),
        )

        result = await coordinator.coordinate(ctx)

        # Not fully successful (wave 0 failed)
        assert not result.is_success
        # Both waves still executed
        assert len(result.waves) == 2
        assert result.status_rollup is not None
        assert result.status_rollup.failed == 1
        assert result.status_rollup.completed == 1

    @pytest.mark.unit
    async def test_task_engine_parent_update(self) -> None:
        """Parent task is updated via TaskEngine when provided."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        task_engine = AsyncMock()
        task_engine.submit.return_value = TaskMutationResult(
            request_id="req-1",
            success=True,
            task=make_assignment_task(
                id="parent-1",
                status=TaskStatus.COMPLETED,
                assigned_to="coordinator",
            ),
            version=2,
        )

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
            task_engine=task_engine,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)

        assert result.is_success
        task_engine.submit.assert_called_once()

    @pytest.mark.unit
    async def test_no_task_engine_skips_update(self) -> None:
        """Without TaskEngine, parent update is skipped."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)

        assert result.is_success
        # No update_parent phase in results
        update_phases = [p for p in result.phases if p.phase == "update_parent"]
        assert len(update_phases) == 0

    @pytest.mark.unit
    async def test_status_rollup_correctness(self) -> None:
        """Status rollup accurately reflects execution outcomes."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "bob"),
            ]
        )

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        # sub-a succeeds, sub-b fails
        outcomes = (
            AgentOutcome(
                task_id="sub-a",
                agent_id=agent_id_a,
                result=build_run_result("sub-a", agent_id_a),
            ),
            AgentOutcome(
                task_id="sub-b",
                agent_id=agent_id_b,
                error="Test failure",
            ),
        )
        exec_result = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=outcomes,
            total_duration_seconds=1.0,
        )

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[exec_result],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )

        result = await coordinator.coordinate(ctx)

        assert result.status_rollup is not None
        assert result.status_rollup.completed == 1
        assert result.status_rollup.failed == 1
        assert result.status_rollup.total == 2
        assert result.status_rollup.derived_parent_status == TaskStatus.FAILED

    @pytest.mark.unit
    async def test_workspace_lifecycle(self) -> None:
        """Workspace setup → execute → merge → teardown lifecycle."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "bob"),
            ]
        )

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        ws_a = Workspace(
            workspace_id="ws-a",
            task_id="sub-a",
            agent_id=agent_id_a,
            branch_name="workspace/sub-a",
            worktree_path="fake/ws-a",
            base_branch="main",
            created_at=datetime.now(UTC),
        )
        ws_b = Workspace(
            workspace_id="ws-b",
            task_id="sub-b",
            agent_id=agent_id_b,
            branch_name="workspace/sub-b",
            worktree_path="fake/ws-b",
            base_branch="main",
            created_at=datetime.now(UTC),
        )

        ws_service = AsyncMock()
        ws_service.setup_group.return_value = (ws_a, ws_b)
        ws_service.merge_group.return_value = WorkspaceGroupResult(
            group_id="merge-1",
            merge_results=(
                MergeResult(
                    workspace_id="ws-a",
                    branch_name="workspace/sub-a",
                    success=True,
                    merged_commit_sha="abc123",
                    duration_seconds=0.1,
                ),
                MergeResult(
                    workspace_id="ws-b",
                    branch_name="workspace/sub-b",
                    success=True,
                    merged_commit_sha="def456",
                    duration_seconds=0.1,
                ),
            ),
            duration_seconds=0.5,
        )
        ws_service.teardown_group.return_value = None

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result(
                    "wave-0",
                    [
                        ("sub-a", agent_id_a),
                        ("sub-b", agent_id_b),
                    ],
                ),
            ],
            workspace_service=ws_service,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )

        result = await coordinator.coordinate(ctx)

        assert result.is_success
        ws_service.setup_group.assert_called_once()
        ws_service.merge_group.assert_called_once()
        ws_service.teardown_group.assert_called_once()
        assert result.workspace_merge is not None
        assert result.workspace_merge.all_merged

    @pytest.mark.unit
    async def test_memory_error_propagated(self) -> None:
        """MemoryError from decomposition is not swallowed."""
        coordinator = _make_coordinator(
            decompose_error=MemoryError("out of memory"),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        with pytest.raises(MemoryError):
            await coordinator.coordinate(ctx)

    @pytest.mark.unit
    async def test_auto_topology_resolves_to_centralized(self) -> None:
        """AUTO topology falls back to CENTRALIZED."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,), topology=CoordinationTopology.AUTO)
        routing = make_routing(
            [("sub-a", "alice")],
            topology=CoordinationTopology.AUTO,
        )
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)
        assert result.topology == CoordinationTopology.CENTRALIZED

    @pytest.mark.unit
    async def test_update_parent_submit_fails(self) -> None:
        """Failed task engine submit is captured as phase failure."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        task_engine = AsyncMock()
        task_engine.submit.return_value = TaskMutationResult(
            request_id="req-1",
            success=False,
            error="transition not allowed",
            error_code="validation",
            version=1,
        )

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
            task_engine=task_engine,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)
        update_phases = [p for p in result.phases if p.phase == "update_parent"]
        assert len(update_phases) == 1
        assert not update_phases[0].success

    @pytest.mark.unit
    async def test_update_parent_exception_captured(self) -> None:
        """TaskEngine exception is captured, not propagated."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        task_engine = AsyncMock()
        task_engine.submit.side_effect = RuntimeError("engine down")

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
            task_engine=task_engine,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)
        update_phases = [p for p in result.phases if p.phase == "update_parent"]
        assert len(update_phases) == 1
        assert not update_phases[0].success
        assert update_phases[0].error is not None
        assert "engine down" in update_phases[0].error

    @pytest.mark.unit
    async def test_rollup_error_captured(self) -> None:
        """Rollup error is captured, not propagated."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        decomp_service = AsyncMock()
        decomp_service.decompose_task.return_value = decomp
        decomp_service.rollup_status = MagicMock(
            side_effect=RuntimeError("rollup broken"),
        )

        routing_service = MagicMock()
        routing_service.route.return_value = routing

        executor = AsyncMock()
        executor.execute_group.side_effect = [
            make_exec_result("wave-0", [("sub-a", agent_id)]),
        ]

        coordinator = MultiAgentCoordinator(
            decomposition_service=decomp_service,
            routing_service=routing_service,
            parallel_executor=executor,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)
        rollup_phases = [p for p in result.phases if p.phase == "rollup"]
        assert len(rollup_phases) == 1
        assert not rollup_phases[0].success
        assert result.status_rollup is None

    @pytest.mark.unit
    async def test_total_cost_aggregated(self) -> None:
        """total_cost_usd sums costs from all waves."""
        from synthorg.providers.models import TokenUsage

        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [("sub-a", "alice"), ("sub-b", "alice")],
        )
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        # Build run results with non-zero costs
        run_a = build_run_result("sub-a", agent_id)
        ctx_a = run_a.execution_result.context.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=100, output_tokens=50, cost_usd=0.05
                )
            }
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        run_a = AgentRunResult(
            execution_result=ExecutionResult(
                context=ctx_a,
                termination_reason=run_a.execution_result.termination_reason,
            ),
            system_prompt=run_a.system_prompt,
            duration_seconds=run_a.duration_seconds,
            agent_id=run_a.agent_id,
            task_id=run_a.task_id,
        )

        run_b = build_run_result("sub-b", agent_id)
        ctx_b = run_b.execution_result.context.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=80, output_tokens=40, cost_usd=0.03
                )
            }
        )
        run_b = AgentRunResult(
            execution_result=ExecutionResult(
                context=ctx_b,
                termination_reason=run_b.execution_result.termination_reason,
            ),
            system_prompt=run_b.system_prompt,
            duration_seconds=run_b.duration_seconds,
            agent_id=run_b.agent_id,
            task_id=run_b.task_id,
        )

        exec_0 = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                AgentOutcome(
                    task_id="sub-a",
                    agent_id=agent_id,
                    result=run_a,
                ),
            ),
            total_duration_seconds=1.0,
        )
        exec_1 = ParallelExecutionResult(
            group_id="wave-1",
            outcomes=(
                AgentOutcome(
                    task_id="sub-b",
                    agent_id=agent_id,
                    result=run_b,
                ),
            ),
            total_duration_seconds=1.0,
        )

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[exec_0, exec_1],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)
        assert result.total_cost_usd == pytest.approx(0.08)

    @pytest.mark.unit
    async def test_fail_fast_stops_after_failed_wave(self) -> None:
        """fail_fast=True stops pipeline after first failed wave."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [("sub-a", "alice"), ("sub-b", "alice")],
        )
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                # Wave 0 fails — should stop before wave 1
                make_exec_result("wave-0", [("sub-a", agent_id)], all_succeed=False),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
            config=CoordinationConfig(fail_fast=True),
        )

        result = await coordinator.coordinate(ctx)

        # Only one wave executed (fail_fast stopped before wave 1)
        assert len(result.waves) == 1
        assert result.status_rollup is not None
        assert result.status_rollup.total == 2
        assert result.status_rollup.failed == 1
        assert result.status_rollup.blocked == 1

    @pytest.mark.unit
    async def test_rollup_includes_blocked_subtasks(self) -> None:
        """Rollup counts unroutable/skipped subtasks as BLOCKED."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        # Only route sub-a; sub-b is unroutable
        routing = make_routing(
            [("sub-a", "alice")],
            unroutable=("sub-b",),
        )
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        result = await coordinator.coordinate(ctx)

        assert result.status_rollup is not None
        # 1 completed + 1 blocked = 2 total
        assert result.status_rollup.total == 2
        assert result.status_rollup.completed == 1
        assert result.status_rollup.blocked == 1

    @pytest.mark.unit
    async def test_dispatch_error_wrapped_as_phase_error(self) -> None:
        """Dispatch failure produces a phase error with partial phases."""
        from synthorg.engine.decomposition.models import (
            DecompositionPlan,
        )
        from synthorg.engine.decomposition.models import (
            DecompositionResult as DecompResult,
        )

        sub_a = make_subtask("sub-a")
        plan = DecompositionPlan(
            parent_task_id="parent-1",
            subtasks=(sub_a,),
            task_structure=TaskStructure.PARALLEL,
            coordination_topology=CoordinationTopology.CENTRALIZED,
        )
        # Bypass validators: created_tasks has wrong ID
        decomp = DecompResult.model_construct(
            plan=plan,
            created_tasks=(
                make_assignment_task(
                    id="sub-x",
                    title="Wrong task",
                    description="Wrong task desc",
                    parent_task_id="parent-1",
                ),
            ),
            dependency_edges=(),
        )
        # Routing targets sub-a, but created_tasks has sub-x
        routing = make_routing([("sub-a", "alice")])

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[],
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        with pytest.raises(CoordinationPhaseError) as exc_info:
            await coordinator.coordinate(ctx)

        assert exc_info.value.phase == "dispatch"
        assert len(exc_info.value.partial_phases) >= 3
