"""A wave is not dispatched on work that did not deliver.

Regression: a live run's first real wave died end to end and every later
wave dispatched anyway, on inputs nobody had written, and failed on its
own. The DAG's edges decided when a subtask ran and never whether it
should.

The dispatcher-level class at the bottom exists because the first fix
reached only two of the three wave loops. The one the live run actually
used, ``ContextDependentDispatcher``, had its own copy of the loop and
went on dispatching, which the next live run showed and no unit test
did. Every dispatcher is now covered by the same parametrised claim.
"""

from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    CoordinationTopology,
    TaskStatus,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import (
    NON_DELIVERING_STATUSES,
    dependency_map,
    unmet_dependencies,
)
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.context_dependent_dispatcher import (
    ContextDependentDispatcher,
)
from synthorg.engine.coordination.dispatcher_types import TopologyDispatcher
from synthorg.engine.coordination.sas_dispatcher import SasDispatcher
from synthorg.engine.coordination.wave_dispatcher import WaveDispatcher
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import (
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import as_uuid, coerce_id, mock_of, sid
from tests.unit.engine.conftest import (
    make_decomposition,
    make_exec_result,
    make_routing,
    make_subtask,
)

pytestmark = pytest.mark.unit


def _identity(label: str) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name="Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


#: Statuses the Task model refuses without somebody holding the row.
_NEEDS_ASSIGNEE: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.IN_REVIEW, TaskStatus.IN_PROGRESS}
)


def _task(label: str, *, status: TaskStatus = TaskStatus.CREATED) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="test-project",
        created_by="test-creator",
        status=status,
        assigned_to=sid("agent-a") if status in _NEEDS_ASSIGNEE else None,
    )


def _group(*assignments: AgentAssignment) -> ParallelExecutionGroup:
    return ParallelExecutionGroup(
        group_id=NotBlankStr("wave-1"),
        assignments=assignments,
    )


def _routing_through_one_agent(labels: tuple[str, ...]) -> RoutingResult:
    """Route every subtask to ONE agent, so the wave splits into rounds.

    ``make_routing`` mints a fresh identity per pair, and rounds are keyed on
    the identity's id, so routing by the same agent NAME still yields one
    round. Sharing the identity is what reproduces the small org staffing a
    single developer.

    Returns:
        A routing result whose decisions all name the same agent.
    """
    agent = _identity("solo-agent")
    return RoutingResult(
        parent_task_id=coerce_id("parent-1"),
        decisions=tuple(
            RoutingDecision(
                subtask_id=coerce_id(label),
                selected_candidate=RoutingCandidate(
                    agent_identity=agent,
                    score=0.9,
                    reason="Only agent on the roster",
                ),
                topology=CoordinationTopology.CENTRALIZED,
            )
            for label in labels
        ),
    )


def _engine(rows: dict[str, Task]) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any
    """An engine holding *rows*, keyed by task id."""

    async def _get(task_id: str) -> Task | None:
        return rows.get(task_id)

    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=_get),
        submit=AsyncMock(
            return_value=TaskMutationResult(request_id="r", success=True, version=2)
        ),
    )


class TestUnmetDependencies:
    """The rule itself, over statuses the engine holds."""

    @pytest.mark.parametrize("status", sorted(NON_DELIVERING_STATUSES))
    def test_non_delivering_status_is_unmet(self, status: TaskStatus) -> None:
        assert unmet_dependencies({"dep-a": status}) == ("dep-a",)

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.COMPLETED,
            TaskStatus.IN_REVIEW,
            TaskStatus.IN_PROGRESS,
            TaskStatus.ASSIGNED,
        ],
    )
    def test_delivering_or_in_flight_status_is_met(self, status: TaskStatus) -> None:
        assert unmet_dependencies({"dep-a": status}) == ()

    def test_in_review_is_met_so_waves_do_not_queue_behind_the_review_gate(
        self,
    ) -> None:
        """Work exists at IN_REVIEW; demanding COMPLETED adds an approval gate.

        A subtask sits IN_REVIEW until a completion gate clears it, so a
        rule requiring COMPLETED would stall every downstream wave on the
        review queue: a second gate nobody declared.
        """
        assert unmet_dependencies({"dep-a": TaskStatus.IN_REVIEW}) == ()

    def test_missing_row_is_unmet(self) -> None:
        """A dependency the engine cannot find has delivered nothing."""
        assert unmet_dependencies({"dep-a": None}) == ("dep-a",)

    def test_names_every_offender_in_a_stable_order(self) -> None:
        assert unmet_dependencies(
            {
                "dep-z": TaskStatus.FAILED,
                "dep-a": TaskStatus.CANCELLED,
                "dep-m": TaskStatus.COMPLETED,
            }
        ) == ("dep-a", "dep-z")

    def test_no_dependencies_is_met(self) -> None:
        assert unmet_dependencies({}) == ()


class TestDependencyMap:
    def test_maps_each_subtask_to_its_declared_dependencies(self) -> None:
        subtasks = (
            SubtaskDefinition(
                id="a",
                title="First",
                description="A detailed first subtask description",
                required_role="Developer",
            ),
            SubtaskDefinition(
                id="b",
                title="Second",
                description="A detailed second subtask description",
                required_role="Developer",
                dependencies=("a",),
            ),
        )
        assert dependency_map(subtasks) == {"a": (), "b": ("a",)}


class TestGateOnDependencies:
    """The wave-dispatch half: what actually runs, and what parks."""

    async def test_subtask_on_a_failed_dependency_does_not_dispatch(self) -> None:
        identity = _identity("agent-a")
        dependent = _task("task-b")
        engine = _engine(
            {
                str(as_uuid("task-a")): _task("task-a", status=TaskStatus.FAILED),
                str(as_uuid("task-b")): dependent,
            }
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=identity, task=dependent)),
            {str(dependent.id): (str(as_uuid("task-a")),)},
        )

        assert gated.assignments == ()

    async def test_the_park_names_the_dependency_and_its_reason(self) -> None:
        identity = _identity("agent-a")
        dependent = _task("task-b")
        failed_id = str(as_uuid("task-a"))
        engine = _engine(
            {
                failed_id: _task("task-a", status=TaskStatus.CANCELLED),
                str(as_uuid("task-b")): dependent,
            }
        )
        writer = AssignmentWriter(engine)

        await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=identity, task=dependent)),
            {str(dependent.id): (failed_id,)},
        )

        mutation = engine.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.BLOCKED
        assert mutation.overrides["blocked_reason"] is BlockedReason.DEPENDENCY_FAILED
        assert failed_id in mutation.reason

    async def test_subtask_whose_dependency_delivered_still_dispatches(self) -> None:
        identity = _identity("agent-a")
        dependent = _task("task-b")
        done_id = str(as_uuid("task-a"))
        engine = _engine(
            {
                done_id: _task("task-a", status=TaskStatus.COMPLETED),
                str(as_uuid("task-b")): dependent,
            }
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=identity, task=dependent)),
            {str(dependent.id): (done_id,)},
        )

        assert len(gated.assignments) == 1
        engine.submit.assert_not_awaited()

    async def test_a_healthy_sibling_still_runs(self) -> None:
        """One dead input parks its own subtask, not the whole wave."""
        blocked_task = _task("task-b")
        healthy_task = _task("task-c")
        engine = _engine(
            {
                str(as_uuid("task-a")): _task("task-a", status=TaskStatus.FAILED),
                str(as_uuid("task-d")): _task("task-d", status=TaskStatus.COMPLETED),
                str(as_uuid("task-b")): blocked_task,
                str(as_uuid("task-c")): healthy_task,
            }
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(
                AgentAssignment(identity=_identity("agent-a"), task=blocked_task),
                AgentAssignment(identity=_identity("agent-b"), task=healthy_task),
            ),
            {
                str(blocked_task.id): (str(as_uuid("task-a")),),
                str(healthy_task.id): (str(as_uuid("task-d")),),
            },
        )

        assert [str(a.task.id) for a in gated.assignments] == [str(healthy_task.id)]

    async def test_a_subtask_with_no_declared_dependencies_dispatches(self) -> None:
        task = _task("task-a")
        engine = _engine({str(task.id): task})
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=_identity("agent-a"), task=task)),
            {},
        )

        assert len(gated.assignments) == 1

    async def test_without_an_engine_the_group_is_unchanged(self) -> None:
        """No engine means no status to read, so nothing can be judged."""
        task = _task("task-a")
        writer = AssignmentWriter(None)
        group = _group(AgentAssignment(identity=_identity("agent-a"), task=task))

        assert await writer.gate_on_dependencies(group, {}) is group

    async def test_a_refused_park_does_not_take_the_wave_down(self) -> None:
        """The healthy siblings still run when a park is rejected."""
        blocked_task = _task("task-b")
        healthy_task = _task("task-c")
        engine = _engine(
            {
                str(as_uuid("task-a")): _task("task-a", status=TaskStatus.FAILED),
                str(as_uuid("task-b")): blocked_task,
                str(as_uuid("task-c")): healthy_task,
            }
        )
        engine.submit = AsyncMock(
            return_value=TaskMutationResult(
                request_id="r",
                success=False,
                error="refused",
                error_code="validation",
            )
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(
                AgentAssignment(identity=_identity("agent-a"), task=blocked_task),
                AgentAssignment(identity=_identity("agent-c"), task=healthy_task),
            ),
            {str(blocked_task.id): (str(as_uuid("task-a")),)},
        )

        assert [str(a.task.id) for a in gated.assignments] == [
            str(blocked_task.id),
            str(healthy_task.id),
        ]

    async def test_a_row_whose_park_was_refused_is_not_dropped(self) -> None:
        """Dropping it is what strands it.

        The gate removes a subtask from the wave because it has been parked
        BLOCKED, which is a status a replan can pick back up. When the park is
        refused the row is still at CREATED, and a CREATED row nothing
        dispatches has no exit and nothing watching it: its plan never derives
        a terminal status and its project can never be deleted. Keeping it in
        the wave costs a turn budget against dead inputs and ends FAILED,
        which is an outcome the rollup can conclude on.
        """
        blocked_task = _task("task-b")
        engine = _engine(
            {
                str(as_uuid("task-a")): _task("task-a", status=TaskStatus.FAILED),
                str(as_uuid("task-b")): blocked_task,
            }
        )
        engine.submit = AsyncMock(
            return_value=TaskMutationResult(
                request_id="r",
                success=False,
                error="refused",
                error_code="validation",
            )
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=_identity("agent-a"), task=blocked_task)),
            {str(blocked_task.id): (str(as_uuid("task-a")),)},
        )

        assert [str(a.task.id) for a in gated.assignments] == [str(blocked_task.id)]

    async def test_a_row_whose_park_persisted_is_dropped(self) -> None:
        """The ordinary path: parked BLOCKED, so the wave does not run it."""
        blocked_task = _task("task-b")
        engine = _engine(
            {
                str(as_uuid("task-a")): _task("task-a", status=TaskStatus.FAILED),
                str(as_uuid("task-b")): blocked_task,
            }
        )
        writer = AssignmentWriter(engine)

        gated = await writer.gate_on_dependencies(
            _group(AgentAssignment(identity=_identity("agent-a"), task=blocked_task)),
            {str(blocked_task.id): (str(as_uuid("task-a")),)},
        )

        assert gated.assignments == ()


#: Every wave loop the product ships, each built around a shared writer.
#:
#: Listed rather than derived because the point is coverage: a dispatcher
#: missing here is a wave loop nobody checked, which is exactly how the live
#: one went ungated while the other two were fixed.
_DISPATCHER_BUILDERS: dict[str, Callable[[AssignmentWriter], TopologyDispatcher]] = {
    "sequential": lambda w: SasDispatcher(assignment_writer=w),
    "centralized": lambda w: WaveDispatcher(
        isolation_required=False,
        topology_label="centralized",
        assignment_writer=w,
    ),
    "context_dependent": lambda w: ContextDependentDispatcher(assignment_writer=w),
}


class TestEveryDispatcherGates:
    """No wave loop dispatches a subtask whose declared inputs died."""

    @pytest.mark.parametrize("topology", sorted(_DISPATCHER_BUILDERS))
    async def test_a_wave_on_a_failed_dependency_never_reaches_the_executor(
        self,
        topology: str,
    ) -> None:
        first = make_subtask("sub-a")
        second = make_subtask("sub-b", dependencies=("sub-a",))
        decomp = make_decomposition((first, second))
        routing = make_routing([("sub-a", "alice"), ("sub-b", "bob")])
        first_id = str(as_uuid("sub-a"))

        rows = {str(t.id): t for t in decomp.created_tasks}
        rows[first_id] = rows[first_id].model_copy(
            update={"status": TaskStatus.FAILED, "assigned_to": None}
        )
        engine = _engine(rows)
        dispatcher = _DISPATCHER_BUILDERS[topology](AssignmentWriter(engine))

        executed: list[str] = []

        async def _execute(group: ParallelExecutionGroup) -> Any:  # type: ignore[explicit-any]  # helper returns a built result
            executed.extend(str(a.task.id) for a in group.assignments)
            return make_exec_result(
                str(group.group_id),
                [(str(a.task.id), a.agent_id) for a in group.assignments],
                all_succeed=False,
            )

        executor = mock_of[ParallelExecutorProtocol](
            execute_group=AsyncMock(side_effect=_execute)
        )

        result = await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(enable_workspace_isolation=False),
        )

        second_id = str(as_uuid("sub-b"))
        assert second_id not in executed
        parked = [
            call.args[0]
            for call in engine.submit.call_args_list
            if call.args[0].target_status == TaskStatus.BLOCKED
        ]
        assert [m.task_id for m in parked] == [second_id]
        assert parked[0].overrides["blocked_reason"] is BlockedReason.DEPENDENCY_FAILED
        # The level the plan did not deliver is a failed phase, never a
        # silence a rollup can read as still working.
        assert any(not phase.success for phase in result.phases)

    @pytest.mark.parametrize("topology", sorted(_DISPATCHER_BUILDERS))
    async def test_a_wave_the_run_never_reached_is_parked_too(
        self,
        topology: str,
    ) -> None:
        """Gating covers the wave dispatched; something must cover the rest.

        A live run stopped after its first wave failed and left two subtasks
        of a later wave at CREATED. No dispatcher would run them, no gate
        would park them, and the rollup needs every item terminal to
        conclude, so the plan sat at ``executing`` for ever and its project
        could not be deleted.
        """
        first = make_subtask("sub-a")
        second = make_subtask("sub-b", dependencies=("sub-a",))
        third = make_subtask("sub-c", dependencies=("sub-b",))
        decomp = make_decomposition((first, second, third))
        routing = make_routing(
            [("sub-a", "alice"), ("sub-b", "bob"), ("sub-c", "carol")]
        )

        rows = {str(t.id): t for t in decomp.created_tasks}
        engine = _engine(rows)
        dispatcher = _DISPATCHER_BUILDERS[topology](AssignmentWriter(engine))

        async def _execute(group: ParallelExecutionGroup) -> Any:  # type: ignore[explicit-any]  # helper returns a built result
            return make_exec_result(
                str(group.group_id),
                [(str(a.task.id), a.agent_id) for a in group.assignments],
                all_succeed=False,
            )

        executor = mock_of[ParallelExecutorProtocol](
            execute_group=AsyncMock(side_effect=_execute)
        )

        await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(enable_workspace_isolation=False, fail_fast=True),
        )

        parked = {
            call.args[0].task_id: call.args[0]
            for call in engine.submit.call_args_list
            if call.args[0].target_status == TaskStatus.BLOCKED
        }
        # Wave 0 failed and the run stopped, so BOTH later waves' subtasks
        # are parked: neither will ever be dispatched, and a CREATED row is
        # a status no rollup can conclude on.
        assert str(as_uuid("sub-b")) in parked
        assert str(as_uuid("sub-c")) in parked
        assert (
            parked[str(as_uuid("sub-c"))].overrides["blocked_reason"]
            is BlockedReason.DEPENDENCY_FAILED
        )

    @pytest.mark.parametrize("topology", sorted(_DISPATCHER_BUILDERS))
    async def test_an_unreached_sibling_says_it_never_started(
        self,
        topology: str,
    ) -> None:
        """A group is one round of AGENTS, not one level of the DAG.

        Two independent subtasks sharing an agent are split across sequential
        groups at the SAME level, so the group after a stop is a sibling of it
        whose declared inputs are untouched. Parking that as a dependency
        failure states something untrue about work that is merely unstarted,
        and a replan reads these reasons and acts on them.
        """
        first = make_subtask("sub-a")
        sibling = make_subtask("sub-b")
        decomp = make_decomposition((first, sibling))
        routing = _routing_through_one_agent(("sub-a", "sub-b"))

        rows = {str(t.id): t for t in decomp.created_tasks}
        engine = _engine(rows)
        dispatcher = _DISPATCHER_BUILDERS[topology](AssignmentWriter(engine))

        async def _execute(group: ParallelExecutionGroup) -> Any:  # type: ignore[explicit-any]  # helper returns a built result
            return make_exec_result(
                str(group.group_id),
                [(str(a.task.id), a.agent_id) for a in group.assignments],
                all_succeed=False,
            )

        executor = mock_of[ParallelExecutorProtocol](
            execute_group=AsyncMock(side_effect=_execute)
        )

        await dispatcher.dispatch(
            decomposition_result=decomp,
            routing_result=routing,
            parallel_executor=executor,
            workspace_service=None,
            config=CoordinationConfig(enable_workspace_isolation=False, fail_fast=True),
        )

        parked = [
            call.args[0]
            for call in engine.submit.call_args_list
            if call.args[0].target_status == TaskStatus.BLOCKED
        ]
        # BOTH rows, on every dispatcher: the wave that raised strands its own
        # undispatched rows (``persist`` gives up on the first refused hop),
        # and the round after it never runs. A row left at CREATED has no exit,
        # so neither may be skipped.
        assert {p.task_id for p in parked} == {
            str(as_uuid("sub-a")),
            str(as_uuid("sub-b")),
        }
        # Neither subtask declares an input, so no park here may say a
        # dependency failed. A replan reads these reasons and would go looking
        # for work to redo.
        assert {p.overrides["blocked_reason"] for p in parked} == {
            BlockedReason.RUN_STOPPED
        }
