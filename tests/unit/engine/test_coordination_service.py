"""Tests for MultiAgentCoordinator service."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from synthorg.engine.decomposition.models import DecompositionResult

from synthorg.budget.coordination_collector import (
    CollectionInputs,
    CoordinationMetricsCollector,
)
from synthorg.budget.coordination_metric_models import CoordinationMetrics
from synthorg.core.task_enums import (
    BlockedReason,
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
)
from synthorg.core.task_transitions import transition_path
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.models import (
    CoordinationContext,
)
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from synthorg.engine.routing.models import (
    RoutingResult,
)
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.engine.workspace.models import (
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
)
from tests._shared import FakeClock, coerce_id, mock_of, sid
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


def _status_engine(  # type: ignore[explicit-any]  # mock_of returns Any
    statuses: dict[str, TaskStatus],
    *,
    parent_id: str = "parent-1",
) -> Any:
    """A task engine resolving each subtask label to its persisted status.

    The rollup phase reads persisted status rather than the dispatch outcome,
    so a coordination test states what the store holds for each subtask.

    A subtask is read twice with different answers, because two different
    moments ask. The wave reads it before dispatching, when the row is
    freshly CREATED (that read is what makes the ``CREATED -> ASSIGNED``
    write, so the persisted row cannot lag the local context); the rollup
    reads it after the run, when the agent's own sync has moved it to the
    declared status. Serving the declared status to both would ask the wave
    to assign an already-completed task.

    Returns:
        A task-engine double whose ``get_task`` serves those rows.
    """
    settled = {
        coerce_id(label): make_assignment_task(
            id=label,
            status=status,
            assigned_to="alice" if status is not TaskStatus.CREATED else None,
        )
        for label, status in statuses.items()
    }
    dispatch_time = {
        task_id: make_assignment_task(id=str(task_id), status=TaskStatus.CREATED)
        for task_id in settled
    }
    settled[coerce_id(parent_id)] = make_assignment_task(id=parent_id)
    read_counts: dict[str, int] = {}

    def _get(task_id: str) -> object | None:
        seen = read_counts.get(task_id, 0)
        read_counts[task_id] = seen + 1
        if seen == 0 and task_id in dispatch_time:
            return dispatch_time[task_id]
        return settled.get(task_id)

    def _submit(mutation: object) -> TaskMutationResult:
        # The assignment writer reads the row back off the result, so a
        # double that accepts the mutation must also return what it wrote.
        task_id = str(getattr(mutation, "task_id", ""))
        overrides = getattr(mutation, "overrides", {}) or {}
        return TaskMutationResult(
            request_id="r",
            success=True,
            task=make_assignment_task(
                id=task_id,
                status=getattr(mutation, "target_status", TaskStatus.ASSIGNED),
                assigned_to=overrides.get("assigned_to", "alice"),
            ),
            version=1,
        )

    # Autospecced against ``TaskEngine``, not a bare mock: the two methods
    # below are the whole contract this double stands in for, and a bare mock
    # answers to any name, so a rename on either would leave a dozen tests
    # silently green against a method the engine no longer has.
    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=_get),
        submit=AsyncMock(side_effect=_submit),
    )


def _make_coordinator(  # noqa: PLR0913
    *,
    decomp_result: DecompositionResult | None = None,
    routing_result: RoutingResult | None = None,
    exec_results: list[ParallelExecutionResult] | None = None,
    workspace_service: AsyncMock | None = None,
    task_engine: AsyncMock | None = None,
    decompose_error: Exception | None = None,
    route_error: Exception | None = None,
    clock: FakeClock | None = None,
    collector: CoordinationMetricsCollector | None = None,
) -> MultiAgentCoordinator:
    """Build a MultiAgentCoordinator with mocked dependencies."""
    decomp_service = AsyncMock(spec=DecompositionService)
    if decompose_error:
        decomp_service.decompose_task.side_effect = decompose_error
    elif decomp_result:
        decomp_service.decompose_task.return_value = decomp_result
    decomp_service.rollup_status = MagicMock()
    if decomp_result:
        from synthorg.engine.decomposition.rollup import StatusRollup

        decomp_service.rollup_status.side_effect = StatusRollup.compute

    routing_service = MagicMock(spec=TaskRoutingService)
    if route_error:
        routing_service.route.side_effect = route_error
    elif routing_result:
        routing_service.route.return_value = routing_result

    executor = AsyncMock(spec=ParallelExecutor)
    if exec_results:
        executor.execute_group.side_effect = exec_results

    return MultiAgentCoordinator(
        decomposition_service=decomp_service,
        routing_service=routing_service,
        parallel_executor=executor,
        workspace_service=workspace_service,
        task_engine=task_engine,
        clock=clock,
        coordination_metrics_collector=collector,
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
            task_engine=_status_engine(
                {
                    "sub-a": TaskStatus.COMPLETED,
                    "sub-b": TaskStatus.COMPLETED,
                }
            ),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert attributed.is_success
        assert result.topology == CoordinationTopology.CENTRALIZED
        assert result.decomposition_result is not None
        assert result.routing_result is not None
        assert len(result.waves) == 1
        assert result.status_rollup is not None
        assert result.status_rollup.completed == 2
        assert result.total_duration_seconds > 0
        assert isinstance(attributed.agent_contributions, tuple)

    @pytest.mark.unit
    async def test_clock_seam_drives_elapsed_deterministically(self) -> None:
        """Injected FakeClock is the sole time source for elapsed instrumentation.

        A non-advancing FakeClock makes every ``monotonic()`` read
        identical, so the pipeline's total duration is exactly 0.0.
        Any residual bare ``time.monotonic()`` would instead produce a
        small positive wall-clock delta, so this asserts the seam.
        """
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)

        fake = FakeClock()
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[make_exec_result("wave-0", [("sub-a", agent_id_a)])],
            clock=fake,
        )
        assert coordinator._clock is fake

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-clock"),
            available_agents=(make_assignment_agent("alice"),),
        )
        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success
        assert attributed.result.total_duration_seconds == 0.0

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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert attributed.is_success
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
            parent_task_id=sid("parent-1"),
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
        """With fail_fast=False, a failure does not stop unrelated later work.

        ``fail_fast=False`` means the run proceeds on what did land, and
        what landed is judged per subtask: ``sub-c`` runs because its own
        input delivered, not because the wave it sits in came after one
        that half-failed.
        """
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        sub_c = make_subtask("sub-c", dependencies=("sub-b",))
        decomp = make_decomposition(
            (sub_a, sub_b, sub_c),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "bob"),
                ("sub-c", "carol"),
            ]
        )

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)
        agent_id_c = str(routing.decisions[2].selected_candidate.agent_identity.id)

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                # Wave 0 runs both roots; sub-a fails, sub-b delivers.
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_id_a), ("sub-b", agent_id_b)],
                    all_succeed=False,
                    succeeded_task_ids=frozenset({"sub-b"}),
                ),
                make_exec_result("wave-1", [("sub-c", agent_id_c)], all_succeed=True),
            ],
            task_engine=_status_engine(
                {
                    "sub-a": TaskStatus.FAILED,
                    "sub-b": TaskStatus.COMPLETED,
                    "sub-c": TaskStatus.COMPLETED,
                }
            ),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
                make_assignment_agent("carol"),
            ),
            config=CoordinationConfig(fail_fast=False),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        # Not fully successful (sub-a failed)
        assert not attributed.is_success
        # The dependent wave still ran: its own input delivered.
        assert len(result.waves) == 2
        assert result.status_rollup is not None
        assert result.status_rollup.failed == 1
        assert result.status_rollup.completed == 2

    @pytest.mark.unit
    async def test_wave_is_not_dispatched_on_work_that_failed(self) -> None:
        """A subtask whose declared input died is parked, never dispatched.

        Regression: a live run's first real wave died end to end and every
        later wave dispatched anyway, on outputs nobody had written, and
        failed on its own. The edges existed, were correct, and decided
        only when a subtask ran.
        """
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])

        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        engine = _status_engine(
            {"sub-a": TaskStatus.FAILED, "sub-b": TaskStatus.BLOCKED}
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[
                make_exec_result("wave-0", [("sub-a", agent_id_a)], all_succeed=False),
                make_exec_result("wave-1", [("sub-b", agent_id_b)], all_succeed=True),
            ],
            task_engine=engine,
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
            config=CoordinationConfig(fail_fast=False),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert not attributed.is_success
        # Wave 1 never ran: it had nothing left to dispatch.
        assert len(result.waves) == 1
        assert [w.wave_index for w in result.waves] == [0]
        wave_1_phase = next(p for p in result.phases if p.phase == "execute_wave_1")
        assert not wave_1_phase.success
        # Filtered on the reason, not on BLOCKED alone: the rollup also parks
        # the PARENT once a child is blocked, and that write is not this one.
        parks = [
            call.args[0]
            for call in engine.submit.call_args_list
            if (call.args[0].overrides or {}).get("blocked_reason")
            is BlockedReason.DEPENDENCY_FAILED
        ]
        assert len(parks) == 1
        assert parks[0].target_status is TaskStatus.BLOCKED
        assert coerce_id("sub-a") in parks[0].reason

    @pytest.mark.unit
    async def test_task_engine_parent_update(self) -> None:
        """Parent task is updated via TaskEngine when provided."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        # The parent is freshly CREATED, so the coordinator walks the
        # full lifecycle (CREATED -> ASSIGNED -> IN_PROGRESS ->
        # IN_REVIEW -> COMPLETED) to reach the COMPLETED rollup status,
        # which the persisted (gate-passed) subtask status derives.
        task_engine = _status_engine({"sub-a": TaskStatus.COMPLETED})

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

        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success
        # The subtask is persisted ASSIGNED before the wave dispatches, then
        # one submit per valid parent lifecycle hop to COMPLETED, in order.
        expected = transition_path(TaskStatus.CREATED, TaskStatus.COMPLETED)
        assert expected is not None
        submitted = [
            call.args[0].target_status for call in task_engine.submit.await_args_list
        ]
        assert submitted == [TaskStatus.ASSIGNED, *expected]

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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert attributed.is_success
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
                task_id=sid("sub-a"),
                agent_id=agent_id_a,
                result=build_run_result("sub-a", agent_id_a),
            ),
            AgentOutcome(
                task_id=sid("sub-b"),
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
            task_engine=_status_engine(
                {
                    "sub-a": TaskStatus.COMPLETED,
                    "sub-b": TaskStatus.FAILED,
                }
            ),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

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
            task_id=sid("sub-a"),
            agent_id=agent_id_a,
            branch_name="workspace/sub-a",
            worktree_path="fake/ws-a",
            base_branch="main",
            created_at=datetime.now(UTC),
        )
        ws_b = Workspace(
            workspace_id="ws-b",
            task_id=sid("sub-b"),
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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert attributed.is_success
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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result
        assert result.topology == CoordinationTopology.CENTRALIZED

    @pytest.mark.unit
    async def test_update_parent_submit_fails(self) -> None:
        """Failed task engine submit is captured as phase failure."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        task_engine = AsyncMock()
        task_engine.get_task.return_value = make_assignment_task(id="parent-1")
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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result
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
        task_engine.get_task.return_value = make_assignment_task(id="parent-1")
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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result
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

        decomp_service = AsyncMock(spec=DecompositionService)
        decomp_service.decompose_task.return_value = decomp
        decomp_service.rollup_status = MagicMock(
            side_effect=RuntimeError("rollup broken"),
        )

        routing_service = MagicMock(spec=TaskRoutingService)
        routing_service.route.return_value = routing

        executor = AsyncMock(spec=ParallelExecutor)
        executor.execute_group.side_effect = [
            make_exec_result("wave-0", [("sub-a", agent_id)]),
        ]

        coordinator = MultiAgentCoordinator(
            decomposition_service=decomp_service,
            routing_service=routing_service,
            parallel_executor=executor,
            task_engine=_status_engine({"sub-a": TaskStatus.COMPLETED}),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result
        rollup_phases = [p for p in result.phases if p.phase == "rollup"]
        assert len(rollup_phases) == 1
        assert not rollup_phases[0].success
        assert result.status_rollup is None

    @pytest.mark.unit
    async def test_total_cost_aggregated(self) -> None:
        """total_cost sums costs from all waves."""
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
                    input_tokens=100, output_tokens=50, cost=0.05
                )
            }
        )
        run_a = run_a.model_copy(
            update={
                "execution_result": run_a.execution_result.model_copy(
                    update={"context": ctx_a},
                ),
            }
        )

        run_b = build_run_result("sub-b", agent_id)
        ctx_b = run_b.execution_result.context.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=80, output_tokens=40, cost=0.03
                )
            }
        )
        run_b = run_b.model_copy(
            update={
                "execution_result": run_b.execution_result.model_copy(
                    update={"context": ctx_b},
                ),
            }
        )

        exec_0 = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                AgentOutcome(
                    task_id=sid("sub-a"),
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
                    task_id=sid("sub-b"),
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

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result
        assert result.total_cost == pytest.approx(0.08)

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
                # Wave 0 fails -- should stop before wave 1
                make_exec_result("wave-0", [("sub-a", agent_id)], all_succeed=False),
            ],
            # sub-b never reached the engine, so it has no persisted row.
            task_engine=_status_engine({"sub-a": TaskStatus.FAILED}),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
            config=CoordinationConfig(fail_fast=True),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

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
            # sub-b was unroutable, so it has no persisted row.
            task_engine=_status_engine({"sub-a": TaskStatus.COMPLETED}),
        )

        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )

        attributed = await coordinator.coordinate(ctx)
        result = attributed.result

        assert result.status_rollup is not None
        # 1 completed + 1 blocked = 2 total
        assert result.status_rollup.total == 2
        assert result.status_rollup.completed == 1
        assert result.status_rollup.blocked == 1

    @pytest.mark.unit
    async def test_an_unroutable_subtask_is_parked_with_its_reason(self) -> None:
        """A filed subtask nobody can take must not be left CREATED.

        Nothing downstream reads ``RoutingResult.unroutable``, so a row left
        CREATED with no assignee has no writer that will ever move it again.
        It simply sits there while the plan reports EXECUTING and the
        dashboard reports all systems normal.
        """
        decomp = make_decomposition((make_subtask("sub-a"), make_subtask("sub-b")))
        routing = make_routing([("sub-a", "alice")], unroutable=("sub-b",))
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)
        engine = _status_engine({"sub-a": TaskStatus.COMPLETED})

        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=[make_exec_result("wave-0", [("sub-a", agent_id)])],
            task_engine=engine,
        )

        await coordinator.coordinate(
            CoordinationContext(
                task=make_assignment_task(id="parent-1"),
                available_agents=(make_assignment_agent("alice"),),
            )
        )

        # Filtered on the reason, not on BLOCKED alone: the rollup also parks
        # the PARENT once a child is blocked, and that write is not this one.
        parks = [
            call.args[0]
            for call in engine.submit.await_args_list
            if (call.args[0].overrides or {}).get("blocked_reason")
            is BlockedReason.NO_CAPABLE_AGENT
        ]
        assert [p.task_id for p in parks] == [sid("sub-b")]
        assert parks[0].target_status is TaskStatus.BLOCKED
        assert parks[0].reason

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
            parent_task_id=sid("parent-1"),
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
                    parent_task_id=sid("parent-1"),
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


class TestCoordinationMetricsCollection:
    """The coordinator computes + records multi-agent metrics post-run."""

    @staticmethod
    def _two_agent_setup() -> tuple[
        DecompositionResult,
        RoutingResult,
        list[ParallelExecutionResult],
        CoordinationContext,
    ]:
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)
        exec_results = [
            make_exec_result(
                "wave-0",
                [("sub-a", agent_id_a), ("sub-b", agent_id_b)],
            ),
        ]
        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
        )
        return decomp, routing, exec_results, ctx

    @pytest.mark.unit
    async def test_multi_agent_collect_invoked(self) -> None:
        decomp, routing, exec_results, ctx = self._two_agent_setup()
        collector = mock_of[CoordinationMetricsCollector](
            collect=AsyncMock(return_value=CoordinationMetrics()),
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=collector,
        )

        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success
        collector.collect.assert_awaited_once()
        inputs = collector.collect.await_args.args[0]
        assert isinstance(inputs, CollectionInputs)
        assert inputs.is_multi_agent is True
        assert inputs.task_id == sid("parent-1")
        assert inputs.team_size == 2
        assert inputs.agent_durations is not None
        assert len(inputs.agent_durations) == 2
        assert all(isinstance(d, tuple) and len(d) == 2 for d in inputs.agent_durations)
        assert isinstance(inputs.agent_outputs, tuple)
        # The aggregate carries the team-wide turn records.
        assert hasattr(inputs.execution_result, "turns")

    @pytest.mark.unit
    async def test_collector_failure_is_never_fatal(self) -> None:
        decomp, routing, exec_results, ctx = self._two_agent_setup()
        collector = mock_of[CoordinationMetricsCollector](
            collect=AsyncMock(side_effect=RuntimeError("collector boom")),
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=collector,
        )

        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success
        collector.collect.assert_awaited_once()

    @pytest.mark.unit
    async def test_no_collector_completes_cleanly(self) -> None:
        decomp, routing, exec_results, ctx = self._two_agent_setup()
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=None,
        )

        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success

    @pytest.mark.unit
    async def test_durations_aggregated_per_agent(self) -> None:
        """One agent across two subtasks yields a single summed entry."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)
        exec_results = [
            make_exec_result(
                "wave-0",
                [("sub-a", agent_id), ("sub-b", agent_id)],
            ),
        ]
        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(make_assignment_agent("alice"),),
        )
        collector = mock_of[CoordinationMetricsCollector](
            collect=AsyncMock(return_value=CoordinationMetrics()),
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=collector,
        )

        attributed = await coordinator.coordinate(ctx)

        assert attributed.is_success
        inputs = collector.collect.await_args.args[0]
        assert isinstance(inputs, CollectionInputs)
        assert inputs.team_size == 1
        assert inputs.agent_durations is not None
        assert len(inputs.agent_durations) == 1
        entry_agent_id, entry_duration = inputs.agent_durations[0]
        assert entry_agent_id == agent_id
        # build_run_result sets duration_seconds=0.5 per subtask; the two
        # subtasks for the same agent must sum rather than appear twice.
        assert entry_duration == pytest.approx(1.0)

    @pytest.mark.unit
    async def test_team_size_counts_failed_participants(self) -> None:
        """A participant whose subtask failed still counts in team_size."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)
        exec_results = [
            make_exec_result("wave-0", [("sub-a", agent_id_a)], all_succeed=False),
            make_exec_result("wave-1", [("sub-b", agent_id_b)], all_succeed=True),
        ]
        ctx = CoordinationContext(
            task=make_assignment_task(id="parent-1"),
            available_agents=(
                make_assignment_agent("alice"),
                make_assignment_agent("bob"),
            ),
            config=CoordinationConfig(fail_fast=False),
        )
        collector = mock_of[CoordinationMetricsCollector](
            collect=AsyncMock(return_value=CoordinationMetrics()),
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=collector,
        )

        await coordinator.coordinate(ctx)

        collector.collect.assert_awaited_once()
        inputs = collector.collect.await_args.args[0]
        assert isinstance(inputs, CollectionInputs)
        # Both agents were dispatched; alice's subtask failed (no result)
        # but still counts toward team_size.
        assert inputs.team_size == 2
        # agent_durations only carries agents that produced a result.
        assert inputs.agent_durations is not None
        assert len(inputs.agent_durations) == 1
        assert inputs.agent_durations[0][0] == agent_id_b

    @pytest.mark.unit
    async def test_collector_timeout_is_never_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A collector exceeding the bounded wait must not fail the run."""
        monkeypatch.setattr(
            "synthorg.budget.coordination_collector.COLLECT_TIMEOUT_SECONDS",
            0.01,
        )
        decomp, routing, exec_results, ctx = self._two_agent_setup()

        async def _hang(_inputs: object) -> CoordinationMetrics:
            # Never completes; the bounded asyncio.wait_for must cancel
            # it and surface TimeoutError into the non-fatal guard.
            await asyncio.Event().wait()
            return CoordinationMetrics()

        collector = mock_of[CoordinationMetricsCollector](
            collect=AsyncMock(side_effect=_hang),
        )
        coordinator = _make_coordinator(
            decomp_result=decomp,
            routing_result=routing,
            exec_results=exec_results,
            collector=collector,
        )

        attributed = await coordinator.coordinate(ctx)

        # The coordination run itself succeeded; the collector timeout
        # is swallowed by the bounded-cleanup guard.
        assert attributed.is_success
        collector.collect.assert_awaited_once()
