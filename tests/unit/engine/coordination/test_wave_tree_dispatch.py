"""A recursive plan reaches the dispatcher as a tree, through the real builder.

Every coordination module switched to the tree-aware views and no coordination
test moved with them. What coverage there was tested ``DependencyGraph`` on the
augmented subtask view directly, which is the layer BELOW the one a dispatcher
calls: ``build_execution_waves`` also resolves routing decisions, looks tasks up
by id and drops what it cannot place, and none of that had been asked a single
question about a container.

So these drive a real two-level ``DecompositionResult`` through the production
entry point and then through the two gates that decide what a wave actually
runs.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import dependency_map
from synthorg.engine.coordination._wave_parking import abandon_unreachable, gate_wave
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.parallel_models import ParallelExecutionGroup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    TaskMutationResult,
    TransitionTaskMutation,
)
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests.unit.engine.conftest import make_routing

pytestmark = pytest.mark.unit

#: The tree these cases run: one container over two leaves, plus an
#: independent leaf that shares no ancestor with either.
_CONTAINER = "engine"
_CHILDREN = ("board", "rotation")
_INDEPENDENT = "ui"


def _subtask(label: str) -> SubtaskDefinition:
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
    )


def _task(label: str, *, status: TaskStatus = TaskStatus.CREATED) -> Task:
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Task {label}"),
        description=NotBlankStr("A detailed test task description"),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr("test-project"),
        created_by=NotBlankStr("test-creator"),
        status=status,
        assigned_to=None if status is TaskStatus.CREATED else str(as_uuid("worker")),
    )


def _level(
    parent: str, labels: tuple[str, ...], *, depth: int
) -> tuple[DecompositionPlan, tuple[Task, ...]]:
    """Build one level's plan and the tasks minted from it.

    Returns:
        The plan and its created tasks.
    """
    subtasks = tuple(_subtask(label) for label in labels)
    plan = DecompositionPlan(
        parent_task_id=NotBlankStr(parent),
        subtasks=subtasks,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
    )
    del depth
    return plan, tuple(_task(label) for label in labels)


def _tree() -> DecompositionResult:
    """Build the two-level decomposition these cases dispatch.

    Returns:
        The tree.
    """
    below_plan, below_tasks = _level(sid(_CONTAINER), _CHILDREN, depth=1)
    root_plan, root_tasks = _level(
        str(as_uuid("objective")), (_CONTAINER, _INDEPENDENT), depth=0
    )
    return DecompositionResult(
        plan=root_plan,
        created_tasks=root_tasks,
        dependency_edges=(),
        depth=0,
        children=(
            DecompositionResult(
                plan=below_plan,
                created_tasks=below_tasks,
                dependency_edges=(),
                depth=1,
            ),
        ),
    )


def _waves(*, unroutable: tuple[str, ...] = ()) -> tuple[ParallelExecutionGroup, ...]:
    """Run the real wave builder over the tree.

    Returns:
        The groups, in the order a dispatcher runs them.
    """
    routable = [
        label
        for label in (_CONTAINER, _INDEPENDENT, *_CHILDREN)
        if sid(label) not in unroutable
    ]
    return build_execution_waves(
        decomposition_result=_tree(),
        routing_result=make_routing(
            [(sid(label), f"agent-{label}") for label in routable],
            parent_task_id=str(as_uuid("objective")),
            unroutable=unroutable,
        ),
        config=CoordinationConfig(),
    )


def _levels(groups: tuple[ParallelExecutionGroup, ...]) -> list[set[str]]:
    """Reduce built groups to the set of task ids in each.

    Returns:
        One entry per group, in execution order.
    """
    return [{str(a.task.id) for a in group.assignments} for group in groups]


class _Engine:
    """A task engine double recording every mutation it was asked for."""

    def __init__(self, rows: dict[str, Task]) -> None:
        self.rows = rows

    async def get_task(self, task_id: str) -> Task | None:
        """Answer the row this engine holds.

        Returns:
            The task, or ``None``.
        """
        return self.rows.get(task_id)

    async def submit(self, mutation: TransitionTaskMutation) -> TaskMutationResult:
        """Accept any mutation.

        Returns:
            A successful result.
        """
        return TaskMutationResult(request_id=mutation.request_id, success=True)


def _engine(rows: dict[str, Task]) -> Any:  # type: ignore[explicit-any]
    """Build the engine double.

    Returns:
        The mock.
    """
    double = _Engine(rows)
    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=double.get_task),
        submit=AsyncMock(side_effect=double.submit),
    )


def _parks(engine: Any) -> list[TransitionTaskMutation]:  # type: ignore[explicit-any]
    """Every mutation the engine was handed.

    Returns:
        The mutations, in order.
    """
    return [call.args[0] for call in engine.submit.await_args_list]


class TestTheBuilderSchedulesTheWholeTree:
    def test_a_container_is_scheduled_strictly_after_its_children(self) -> None:
        levels = _levels(_waves())
        at = {task_id: index for index, level in enumerate(levels) for task_id in level}
        assert at[str(as_uuid("board"))] < at[str(as_uuid(_CONTAINER))]
        assert at[str(as_uuid("rotation"))] < at[str(as_uuid(_CONTAINER))]

    def test_an_independent_leaf_runs_beside_the_subtree(self) -> None:
        # A per-subtree loop walked deepest-first would serialise these; the
        # one global computation is what keeps cross-subtree parallelism.
        assert {str(as_uuid("board")), str(as_uuid(_INDEPENDENT))} <= _levels(_waves())[
            0
        ]

    def test_every_node_of_the_tree_reaches_a_wave(self) -> None:
        # A node in no wave is one no dispatcher runs, no gate parks and no
        # rollup can conclude on.
        scheduled = {task_id for level in _levels(_waves()) for task_id in level}
        assert scheduled == {
            str(as_uuid(label)) for label in (_CONTAINER, _INDEPENDENT, *_CHILDREN)
        }


class TestAContainerWaitsOnWhatItAssembles:
    async def _gate_the_container_wave(self, child_status: TaskStatus) -> Any:  # type: ignore[explicit-any]
        """Gate the wave holding the container, with a child at *child_status*.

        Returns:
            The engine double, for the parks it was asked for.
        """
        container_group = next(
            group
            for group in _waves()
            if any(str(a.task.id) == sid(_CONTAINER) for a in group.assignments)
        )
        engine = _engine(
            {
                sid(_CONTAINER): _task(_CONTAINER),
                sid("board"): _task("board", status=child_status),
                sid("rotation"): _task("rotation", status=TaskStatus.COMPLETED),
            }
        )
        await gate_wave(
            container_group,
            wave_idx=1,
            assignment_writer=AssignmentWriter(engine),
            dependencies=dependency_map(_tree().dispatch_subtasks),
            clock=FakeClock(),
            start=0.0,
            phases=[],
        )
        return engine

    async def test_a_dead_child_parks_its_container(self) -> None:
        engine = await self._gate_the_container_wave(TaskStatus.FAILED)

        parks = _parks(engine)
        assert [p.task_id for p in parks] == [str(as_uuid(_CONTAINER))]
        assert parks[0].overrides["blocked_reason"] is BlockedReason.DEPENDENCY_FAILED

    async def test_the_park_names_the_child_it_waited_on(self) -> None:
        # "the assembly did wait on that child" is only an honest reason if the
        # row says WHICH child, which is what an operator acts on.
        engine = await self._gate_the_container_wave(TaskStatus.FAILED)

        assert str(as_uuid("board")) in _parks(engine)[0].reason

    async def test_delivered_children_leave_the_container_alone(self) -> None:
        engine = await self._gate_the_container_wave(TaskStatus.COMPLETED)

        assert _parks(engine) == []


class TestAnUnplaceableContainerStrandsNothing:
    async def test_its_whole_subtree_is_parked_rather_than_left_at_created(
        self,
    ) -> None:
        # The builder drops a subtask routing cannot place AND everything
        # standing on it, into a set local to the build. Those rows are in no
        # group, so only this park reaches them, and without it a recovery
        # sweep re-drives the plan every cadence and changes nothing.
        every_label = (_CONTAINER, _INDEPENDENT, *_CHILDREN)
        engine = _engine({sid(label): _task(label) for label in every_label})

        await abandon_unreachable(
            _waves(unroutable=(sid("board"),)),
            subtask_ids=[sid(label) for label in every_label],
            writer=AssignmentWriter(engine),
        )

        parked = {p.task_id for p in _parks(engine)}
        assert sid("board") in parked
        assert sid(_INDEPENDENT) not in parked

    async def test_the_container_standing_on_it_is_parked_too(self) -> None:
        # The transitive half: a subtree whose leaf cannot be placed leaves its
        # assembly with nothing to assemble, and the builder drops it for the
        # same reason. Parking one and not the other still strands the plan.
        every_label = (_CONTAINER, _INDEPENDENT, *_CHILDREN)
        engine = _engine({sid(label): _task(label) for label in every_label})

        await abandon_unreachable(
            _waves(unroutable=(sid("board"),)),
            subtask_ids=[sid(label) for label in every_label],
            writer=AssignmentWriter(engine),
        )

        assert sid(_CONTAINER) in {p.task_id for p in _parks(engine)}
