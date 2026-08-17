"""A wave is not dispatched on work that did not deliver.

Regression: a live run's first real wave died end to end and every later
wave dispatched anyway, on inputs nobody had written, and failed on its
own. The DAG's edges decided when a subtask ran and never whether it
should.
"""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import BlockedReason, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import (
    NON_DELIVERING_STATUSES,
    dependency_map,
    unmet_dependencies,
)
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import as_uuid, mock_of, sid

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

        assert gated.assignments == ()
