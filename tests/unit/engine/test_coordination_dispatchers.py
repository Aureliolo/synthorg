"""Tests for coordination dispatchers."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import CoordinationTopology, TaskStructure
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.dispatchers import (
    CentralizedDispatcher,
    ContextDependentDispatcher,
    DecentralizedDispatcher,
    DispatchResult,
    SasDispatcher,
    TopologyDispatcher,
    select_dispatcher,
)
from synthorg.engine.workspace.models import (
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
)
from tests.unit.engine.conftest import (
    make_decomposition,
    make_exec_result,
    make_routing,
    make_subtask,
)

if TYPE_CHECKING:
    from synthorg.engine.parallel_models import ParallelExecutionResult

# ── Helpers ─────────────────────────────────────────────────────


def _mock_executor(
    exec_results: list[ParallelExecutionResult] | None = None,
) -> AsyncMock:
    """Create a mock ParallelExecutor."""
    mock = AsyncMock()
    if exec_results:
        mock.execute_group.side_effect = exec_results
    return mock


def _mock_workspace_service(
    workspaces: tuple[Workspace, ...] = (),
    merge_result: WorkspaceGroupResult | None = None,
) -> AsyncMock:
    """Create a mock WorkspaceIsolationService."""
    mock = AsyncMock()
    mock.setup_group.return_value = workspaces
    mock.merge_group.return_value = merge_result or WorkspaceGroupResult(
        group_id="merge-1",
        merge_results=tuple(
            MergeResult(
                workspace_id=ws.workspace_id,
                branch_name=ws.branch_name,
                success=True,
                merged_commit_sha="abc123",
                duration_seconds=0.1,
            )
            for ws in workspaces
        ),
        duration_seconds=0.5,
    )
    mock.teardown_group.return_value = None
    return mock


# ── Tests ───────────────────────────────────────────────────────


class TestSelectDispatcher:
    """select_dispatcher factory tests."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("topology", "expected_type"),
        [
            (CoordinationTopology.SAS, SasDispatcher),
            (CoordinationTopology.CENTRALIZED, CentralizedDispatcher),
            (CoordinationTopology.DECENTRALIZED, DecentralizedDispatcher),
            (CoordinationTopology.CONTEXT_DEPENDENT, ContextDependentDispatcher),
        ],
    )
    def test_returns_correct_dispatcher(
        self,
        topology: CoordinationTopology,
        expected_type: type,
    ) -> None:
        """Factory returns correct dispatcher type."""
        dispatcher = select_dispatcher(topology)
        assert isinstance(dispatcher, expected_type)

    @pytest.mark.unit
    def test_auto_topology_raises(self) -> None:
        """AUTO topology raises ValueError."""
        with pytest.raises(ValueError, match="AUTO must be resolved"):
            select_dispatcher(CoordinationTopology.AUTO)

    @pytest.mark.unit
    def test_all_dispatchers_satisfy_protocol(self) -> None:
        """All dispatchers satisfy the TopologyDispatcher protocol."""
        for topo in (
            CoordinationTopology.SAS,
            CoordinationTopology.CENTRALIZED,
            CoordinationTopology.DECENTRALIZED,
            CoordinationTopology.CONTEXT_DEPENDENT,
        ):
            dispatcher = select_dispatcher(topo)
            assert isinstance(dispatcher, TopologyDispatcher)


class TestSasDispatcher:
    """SasDispatcher tests."""

    @pytest.mark.unit
    async def test_sequential_execution(self) -> None:
        """SAS executes subtasks as sequential waves."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing(
            [
                ("sub-a", "alice"),
                ("sub-b", "alice"),
            ]
        )

        # One result per wave (2 sequential waves)
        agent_id_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_id_b = str(routing.decisions[1].selected_candidate.agent_identity.id)
        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id_a)]),
                make_exec_result("wave-1", [("sub-b", agent_id_b)]),
            ]
        )

        dispatcher = SasDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 2
        assert result.workspaces == ()
        assert result.workspace_merge is None
        assert executor.execute_group.call_count == 2

    @pytest.mark.unit
    async def test_no_workspace_isolation(self) -> None:
        """SAS does not use workspace isolation."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        ws_service = _mock_workspace_service()
        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ]
        )

        dispatcher = SasDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        # SAS never calls workspace service
        ws_service.setup_group.assert_not_called()
        assert result.workspaces == ()


class TestCentralizedDispatcher:
    """CentralizedDispatcher tests."""

    @pytest.mark.unit
    async def test_parallel_waves_with_isolation(self) -> None:
        """Centralized uses DAG waves and workspace isolation."""
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

        ws_service = _mock_workspace_service(
            workspaces=(ws_a, ws_b),
        )
        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [
                        ("sub-a", agent_id_a),
                        ("sub-b", agent_id_b),
                    ],
                ),
            ]
        )

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 1
        assert len(result.workspaces) == 2
        ws_service.setup_group.assert_called_once()
        ws_service.merge_group.assert_called_once()
        ws_service.teardown_group.assert_called_once()

    @pytest.mark.unit
    async def test_no_workspace_service(self) -> None:
        """Centralized works without workspace service."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ]
        )

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 1
        assert result.workspaces == ()

    @pytest.mark.unit
    async def test_workspace_isolation_disabled(self) -> None:
        """Centralized skips isolation when disabled in config."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        ws_service = _mock_workspace_service()
        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ]
        )

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(enable_workspace_isolation=False),
        )

        ws_service.setup_group.assert_not_called()
        assert result.workspaces == ()

    @pytest.mark.unit
    async def test_teardown_on_execution_error(self) -> None:
        """Workspaces are torn down even if execution raises."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])

        ws_a = Workspace(
            workspace_id="ws-a",
            task_id="sub-a",
            agent_id="alice",
            branch_name="workspace/sub-a",
            worktree_path="fake/ws-a",
            base_branch="main",
            created_at=datetime.now(UTC),
        )
        ws_service = _mock_workspace_service(workspaces=(ws_a,))

        executor = AsyncMock()
        executor.execute_group.side_effect = RuntimeError("boom")

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        # Teardown still called
        ws_service.teardown_group.assert_called_once()
        # Wave phase captured the error
        exec_phases = [p for p in result.phases if p.phase.startswith("execute")]
        assert any(not p.success for p in exec_phases)


class TestDecentralizedDispatcher:
    """DecentralizedDispatcher tests."""

    @pytest.mark.unit
    async def test_no_workspace_service_raises(self) -> None:
        """DecentralizedDispatcher raises when workspace_service is None."""
        from synthorg.engine.errors import CoordinationError

        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])

        dispatcher = DecentralizedDispatcher()
        with pytest.raises(CoordinationError, match="workspace isolation"):
            await dispatcher.dispatch(
                decomposition_result=decomp,
                routing_result=routing,
                parallel_executor=_mock_executor(),
                workspace_service=None,
                config=CoordinationConfig(),
            )

    @pytest.mark.unit
    async def test_isolation_disabled_raises(self) -> None:
        """DecentralizedDispatcher raises when isolation is disabled."""
        from synthorg.engine.errors import CoordinationError

        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])

        dispatcher = DecentralizedDispatcher()
        with pytest.raises(CoordinationError, match="workspace isolation"):
            await dispatcher.dispatch(
                decomposition_result=decomp,
                routing_result=routing,
                parallel_executor=_mock_executor(),
                workspace_service=_mock_workspace_service(),
                config=CoordinationConfig(enable_workspace_isolation=False),
            )

    @pytest.mark.unit
    async def test_single_wave_all_parallel(self) -> None:
        """Decentralized puts everything in parallel waves per DAG."""
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
        ws_service = _mock_workspace_service(workspaces=(ws_a, ws_b))

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [
                        ("sub-a", agent_id_a),
                        ("sub-b", agent_id_b),
                    ],
                ),
            ]
        )

        dispatcher = DecentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 1
        assert len(result.workspaces) == 2
        ws_service.setup_group.assert_called_once()
        ws_service.merge_group.assert_called_once()
        ws_service.teardown_group.assert_called_once()


class TestContextDependentDispatcher:
    """ContextDependentDispatcher tests."""

    @pytest.mark.unit
    async def test_single_subtask_wave_no_isolation(self) -> None:
        """Single-subtask waves skip workspace isolation."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        ws_service = _mock_workspace_service()
        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id)]),
            ]
        )

        dispatcher = ContextDependentDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 1
        # No workspace setup for single-subtask wave
        ws_service.setup_group.assert_not_called()

    @pytest.mark.unit
    async def test_multi_subtask_wave_uses_isolation(self) -> None:
        """Multi-subtask waves use workspace isolation."""
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
        ws_service = _mock_workspace_service(workspaces=(ws_a, ws_b))

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [
                        ("sub-a", agent_id_a),
                        ("sub-b", agent_id_b),
                    ],
                ),
            ]
        )

        dispatcher = ContextDependentDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.waves) == 1
        ws_service.setup_group.assert_called_once()
        # Per-wave merge
        ws_service.merge_group.assert_called_once()


class TestCentralizedWorkspaceFailure:
    """CentralizedDispatcher workspace setup failure tests."""

    @pytest.mark.unit
    async def test_workspace_setup_failure_returns_early(self) -> None:
        """CentralizedDispatcher returns early when workspace setup fails."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])

        ws_service = AsyncMock()
        ws_service.setup_group.side_effect = RuntimeError("setup failed")

        executor = _mock_executor()

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.phases) == 1
        assert result.phases[0].phase == "workspace_setup"
        assert not result.phases[0].success
        executor.execute_group.assert_not_called()
        assert len(result.waves) == 0


class TestContextDependentFailFast:
    """ContextDependentDispatcher fail_fast behavior tests."""

    @pytest.mark.unit
    async def test_fail_fast_stops_on_wave_failure(self) -> None:
        """fail_fast=True stops after first failed wave."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing([("sub-a", "alice"), ("sub-b", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result("wave-0", [("sub-a", agent_id)], all_succeed=False),
                make_exec_result("wave-1", [("sub-b", agent_id)]),
            ]
        )

        dispatcher = ContextDependentDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(fail_fast=True),
        )

        # Only first wave executed
        assert len(result.waves) == 1
        assert executor.execute_group.call_count == 1

    @pytest.mark.unit
    async def test_setup_failure_skips_wave(self) -> None:
        """CDD skips wave when workspace setup fails (fail_fast=False)."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])

        ws_service = AsyncMock()
        ws_service.setup_group.side_effect = RuntimeError("setup failed")

        executor = _mock_executor()

        dispatcher = ContextDependentDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(fail_fast=False),
        )

        # Setup failed, wave skipped, no execution
        setup_phases = [
            p for p in result.phases if p.phase.startswith("workspace_setup")
        ]
        assert len(setup_phases) == 1
        assert not setup_phases[0].success
        executor.execute_group.assert_not_called()
        assert len(result.waves) == 0

    @pytest.mark.unit
    async def test_fail_fast_stops_on_setup_failure(self) -> None:
        """fail_fast=True stops when workspace setup fails."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])

        ws_service = AsyncMock()
        ws_service.setup_group.side_effect = RuntimeError("setup failed")

        executor = _mock_executor()

        dispatcher = ContextDependentDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(fail_fast=True),
        )

        # Setup failed, pipeline stopped
        setup_phases = [
            p for p in result.phases if p.phase.startswith("workspace_setup")
        ]
        assert len(setup_phases) == 1
        assert not setup_phases[0].success
        executor.execute_group.assert_not_called()


class TestDecentralizedWorkspaceSetupFailure:
    """DecentralizedDispatcher workspace setup failure tests."""

    @pytest.mark.unit
    async def test_workspace_setup_failure_returns_early(self) -> None:
        """DecentralizedDispatcher returns early on workspace setup failure."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])

        ws_service = AsyncMock()
        ws_service.setup_group.side_effect = RuntimeError("setup failed")

        executor = _mock_executor()

        dispatcher = DecentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        assert len(result.phases) == 1
        assert result.phases[0].phase == "workspace_setup"
        assert not result.phases[0].success
        executor.execute_group.assert_not_called()
        assert len(result.waves) == 0


class TestExecuteWavesExceptionContinuation:
    """Tests for _execute_waves exception handling with fail_fast=False."""

    @pytest.mark.unit
    async def test_exception_with_fail_fast_off_continues(self) -> None:
        """Exception in a wave with fail_fast=False continues to next wave."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing([("sub-a", "alice"), ("sub-b", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = AsyncMock()
        # Wave 0 raises an exception, wave 1 succeeds
        executor.execute_group.side_effect = [
            RuntimeError("boom"),
            make_exec_result("wave-1", [("sub-b", agent_id)]),
        ]

        dispatcher = SasDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(fail_fast=False),
        )

        # Both waves attempted
        assert len(result.waves) == 2
        assert executor.execute_group.call_count == 2
        # First wave has no execution_result (exception)
        assert result.waves[0].execution_result is None
        # Second wave succeeded
        assert result.waves[1].execution_result is not None

    @pytest.mark.unit
    async def test_exception_with_fail_fast_on_stops(self) -> None:
        """Exception in a wave with fail_fast=True stops pipeline."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition(
            (sub_a, sub_b),
            structure=TaskStructure.SEQUENTIAL,
        )
        routing = make_routing([("sub-a", "alice"), ("sub-b", "alice")])

        executor = AsyncMock()
        executor.execute_group.side_effect = RuntimeError("boom")

        dispatcher = SasDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(fail_fast=True),
        )

        # Only first wave attempted
        assert len(result.waves) == 1
        assert executor.execute_group.call_count == 1
        assert result.waves[0].execution_result is None


class TestMergeGating:
    """Tests for merge gating — merge skipped when waves fail."""

    @pytest.mark.unit
    async def test_centralized_skips_merge_on_wave_failure(self) -> None:
        """CentralizedDispatcher skips merge when wave fails."""
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        ws_a = Workspace(
            workspace_id="ws-a",
            task_id="sub-a",
            agent_id=agent_id,
            branch_name="workspace/sub-a",
            worktree_path="fake/ws-a",
            base_branch="main",
            created_at=datetime.now(UTC),
        )
        ws_service = _mock_workspace_service(workspaces=(ws_a,))

        executor = _mock_executor(
            [make_exec_result("wave-0", [("sub-a", agent_id)], all_succeed=False)]
        )

        dispatcher = CentralizedDispatcher()
        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        # Merge NOT called because wave failed
        ws_service.merge_group.assert_not_called()
        # Teardown still called
        ws_service.teardown_group.assert_called_once()
        assert result.workspace_merge is None


class TestDispatchResult:
    """DispatchResult model tests."""

    @pytest.mark.unit
    def test_empty_defaults(self) -> None:
        """DispatchResult defaults are empty."""
        result = DispatchResult()
        assert result.waves == ()
        assert result.workspaces == ()
        assert result.workspace_merge is None
        assert result.phases == ()
