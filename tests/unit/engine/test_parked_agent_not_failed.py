"""A parked agent is waiting on a human, not a failure.

An agent whose run terminates ``PARKED`` has escalated a tool call for a human
decision. It has not failed: the task is alive, an approval is pending, and the
run resumes into its own workspace once the human answers. Conflating the two
kills the plan while the approval is still open, so approving it afterwards
decides nothing.

Three invariants these tests hold: a wave whose only non-successes are parks is
not a failed wave; a parked agent's workspace outlives the wave that started it,
including when the dispatch is cancelled underneath it; and the waves after a
park do not run, because they were scheduled on the promise that it finished.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.context_dependent_dispatcher import (
    ContextDependentDispatcher,
)
from synthorg.engine.coordination.wave_dispatcher import WaveDispatcher
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.parallel_models import AgentOutcome, ParallelExecutionResult
from synthorg.engine.workspace.models import Workspace
from tests._shared import coerce_id
from tests.unit.engine.conftest import (
    build_run_result,
    make_decomposition,
    make_exec_result,
    make_routing,
    make_subtask,
)
from tests.unit.engine.test_coordination_dispatchers import (
    _mock_executor,
    _mock_workspace_service,
)

pytestmark = pytest.mark.unit


def _outcome(task_id: str, agent_id: str, reason: TerminationReason) -> AgentOutcome:
    canonical = coerce_id(task_id)
    return AgentOutcome(
        task_id=canonical,
        agent_id=agent_id,
        result=build_run_result(canonical, agent_id, reason=reason),
    )


def _workspace(task_id: str, agent_id: str, label: str) -> Workspace:
    # ``coerce_id`` mirrors production: the routing decision's subtask id is
    # both the workspace's ``task_id`` and the outcome's, so they join.
    return Workspace(
        workspace_id=f"ws-{label}",
        task_id=coerce_id(task_id),
        agent_id=agent_id,
        branch_name=f"workspace/{label}",
        worktree_path=f"fake/ws-{label}",
        base_branch="main",
        created_at=datetime.now(UTC),
    )


class TestRunResultClassification:
    """``PARKED`` is a suspension, and every consumer must read it as one."""

    @pytest.mark.parametrize(
        ("reason", "awaiting"),
        [
            (TerminationReason.PARKED, True),
            (TerminationReason.COMPLETED, False),
            (TerminationReason.ERROR, False),
            (TerminationReason.NO_OP, False),
            (TerminationReason.MAX_TURNS, False),
            (TerminationReason.CANCELLED, False),
        ],
    )
    def test_only_parked_is_awaiting_human(
        self,
        reason: TerminationReason,
        awaiting: bool,
    ) -> None:
        result = build_run_result("task-1", "agent-1", reason=reason)
        assert result.is_awaiting_human is awaiting

    def test_parked_is_not_a_success(self) -> None:
        result = build_run_result("task-1", "agent-1", reason=TerminationReason.PARKED)
        assert result.is_success is False


class TestGroupAccounting:
    """A park is counted apart from a failure, never folded into one."""

    def test_park_is_not_counted_as_a_failure(self) -> None:
        group = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                _outcome("task-a", "agent-a", TerminationReason.COMPLETED),
                _outcome("task-b", "agent-b", TerminationReason.PARKED),
            ),
            total_duration_seconds=1.0,
        )

        assert group.agents_succeeded == 1
        assert group.agents_awaiting_human == 1
        assert group.agents_failed == 0
        assert group.any_failed is False
        # A park is still not a success: the wave is incomplete, not done.
        assert group.all_succeeded is False

    def test_a_real_failure_is_still_a_failure(self) -> None:
        group = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                _outcome("task-a", "agent-a", TerminationReason.NO_OP),
                _outcome("task-b", "agent-b", TerminationReason.PARKED),
            ),
            total_duration_seconds=1.0,
        )

        assert group.agents_failed == 1
        assert group.agents_awaiting_human == 1
        assert group.any_failed is True

    def test_an_errored_outcome_with_no_result_is_a_failure(self) -> None:
        group = ParallelExecutionResult(
            group_id="wave-0",
            outcomes=(
                AgentOutcome(task_id="task-a", agent_id="agent-a", error="boom"),
            ),
            total_duration_seconds=1.0,
        )

        assert group.agents_failed == 1
        assert group.agents_awaiting_human == 0
        assert group.any_failed is True


class TestWaveDispatch:
    """A wave that is merely waiting on a human has not failed."""

    async def test_wave_with_only_parks_is_not_failed(self) -> None:
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_id)],
                    parked_task_ids=frozenset({"sub-a"}),
                ),
            ]
        )

        result = await WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
        ).dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(),
        )

        execute_phases = [
            p for p in result.phases if p.phase.startswith("execute_wave")
        ]
        assert execute_phases, "the wave must record an execute phase"
        assert all(p.success for p in execute_phases)
        assert all(p.error is None for p in execute_phases)

    async def test_a_parked_agents_workspace_is_not_torn_down(self) -> None:
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        ws_a = _workspace("sub-a", agent_a, "a")
        ws_b = _workspace("sub-b", agent_b, "b")
        ws_service = _mock_workspace_service(workspaces=(ws_a, ws_b))
        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_a), ("sub-b", agent_b)],
                    parked_task_ids=frozenset({"sub-b"}),
                ),
            ]
        )

        await WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
        ).dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=ws_service,
            config=CoordinationConfig(),
        )

        ws_service.teardown_group.assert_called_once()
        torn_down = ws_service.teardown_group.call_args.kwargs["workspaces"]
        torn_down_ids = {w.workspace_id for w in torn_down}
        assert ws_b.workspace_id not in torn_down_ids, (
            "the parked agent resumes into its workspace; tearing it down "
            "leaves the pending approval with nothing to resume"
        )
        assert ws_a.workspace_id in torn_down_ids

    async def test_a_park_stops_the_waves_that_depend_on_it(self) -> None:
        """Wave 1 was scheduled on the promise that wave 0 finished."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_a = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_a)],
                    parked_task_ids=frozenset({"sub-a"}),
                ),
            ]
        )

        result = await WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
        ).dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(),
        )

        # One call only: the second wave never ran, so the executor's
        # side_effect list was never exhausted into a StopIteration either.
        assert executor.execute_group.await_count == 1
        assert len(result.waves) == 1

    async def test_a_cancelled_dispatch_keeps_an_earlier_parked_workspace(
        self,
    ) -> None:
        """Cancellation is a BaseException, so teardown must read the waves."""
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        ws_a = _workspace("sub-a", agent_a, "a")
        ws_b = _workspace("sub-b", agent_b, "b")
        ws_service = _mock_workspace_service(workspaces=(ws_a, ws_b))
        executor = _mock_executor()
        executor.execute_group.side_effect = [
            make_exec_result(
                "wave-0",
                [("sub-a", agent_a)],
                parked_task_ids=frozenset({"sub-a"}),
            ),
            asyncio.CancelledError(),
        ]

        # The park already stops the run, so drive the cancellation through
        # the merge instead: it is the same unwind, one step later.
        ws_service.merge_group.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await WaveDispatcher(
                isolation_required=False,
                topology_label="centralized",
            ).dispatch(
                decomposition_result=decomp,
                routing_result=routing,
                parallel_executor=executor,
                workspace_service=ws_service,
                config=CoordinationConfig(),
            )

        ws_service.teardown_group.assert_called_once()
        torn_down = ws_service.teardown_group.call_args.kwargs["workspaces"]
        assert ws_a.workspace_id not in {w.workspace_id for w in torn_down}

    async def test_a_genuine_failure_still_fails_the_wave(self) -> None:
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [make_exec_result("wave-0", [("sub-a", agent_id)], all_succeed=False)]
        )

        result = await WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
        ).dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(),
        )

        execute_phases = [
            p for p in result.phases if p.phase.startswith("execute_wave")
        ]
        assert execute_phases
        assert not any(p.success for p in execute_phases)


class TestContextDependentDispatch:
    """The second dispatcher has its own copy of the wave loop.

    Two copies of a rule is how two dispatchers came to disagree about it,
    so the parked-outcome behaviour is asserted here as well as against
    ``WaveDispatcher``.
    """

    async def test_a_wave_with_only_parks_is_not_failed(self) -> None:
        sub_a = make_subtask("sub-a")
        decomp = make_decomposition((sub_a,))
        routing = make_routing([("sub-a", "alice")])
        agent_id = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_id)],
                    parked_task_ids=frozenset({"sub-a"}),
                ),
            ]
        )

        result = await ContextDependentDispatcher().dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=_mock_workspace_service(),
            config=CoordinationConfig(),
        )

        execute_phases = [
            p for p in result.phases if p.phase.startswith("execute_wave")
        ]
        assert execute_phases
        assert all(p.success for p in execute_phases)

    async def test_a_park_stops_the_waves_that_depend_on_it(self) -> None:
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_a = str(routing.decisions[0].selected_candidate.agent_identity.id)

        executor = _mock_executor(
            [
                make_exec_result(
                    "wave-0",
                    [("sub-a", agent_a)],
                    parked_task_ids=frozenset({"sub-a"}),
                ),
            ]
        )

        result = await ContextDependentDispatcher().dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=_mock_workspace_service(),
            config=CoordinationConfig(),
        )

        assert executor.execute_group.await_count == 1
        assert len(result.waves) == 1

    async def test_a_cancelled_wave_is_not_merged(self) -> None:
        """Cancellation is a BaseException; "not failed" has to be earned.

        Taking the merge branch on an unwind pushes half-written work from
        a wave that never reported, and tears down the workspace a pending
        approval has to resume into.
        """
        sub_a = make_subtask("sub-a")
        sub_b = make_subtask("sub-b")
        decomp = make_decomposition((sub_a, sub_b))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        agent_a = str(routing.decisions[0].selected_candidate.agent_identity.id)
        agent_b = str(routing.decisions[1].selected_candidate.agent_identity.id)

        ws_service = _mock_workspace_service(
            workspaces=(
                _workspace("sub-a", agent_a, "a"),
                _workspace("sub-b", agent_b, "b"),
            )
        )
        executor = _mock_executor()
        executor.execute_group.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await ContextDependentDispatcher().dispatch(
                decomposition_result=decomp,
                routing_result=routing,
                parallel_executor=executor,
                workspace_service=ws_service,
                config=CoordinationConfig(),
            )

        ws_service.merge_group.assert_not_called()
