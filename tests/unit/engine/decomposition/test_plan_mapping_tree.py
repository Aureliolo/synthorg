"""The projection between a decomposition tree and a durable plan.

``items_from_decomposition`` read one level and ``decomposition_from_plan``
could only build one, so a recursive plan could be produced and never
persisted, and never rebuilt. These cover both directions and the round trip
an operator's edit passes through.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Priority,
    Stakes,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.plan_mapping import (
    PlanProvenance,
    decomposition_from_plan,
    items_from_decomposition,
    plan_from_decomposition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def _objective() -> Task:
    return Task(
        id=as_uuid("objective"),
        title=NotBlankStr("Ship the game"),
        description=NotBlankStr("A playable browser game"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr("beachhead"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.CREATED,
    )


def _subtask(label: str) -> SubtaskDefinition:
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        required_role=NotBlankStr("Developer"),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
    )


def _child_task(label: str, *, parent: Task) -> Task:
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        type=parent.type,
        priority=parent.priority,
        project=parent.project,
        created_by=parent.created_by,
        parent_task_id=NotBlankStr(str(parent.id)),
        status=TaskStatus.CREATED,
    )


def _node(
    *,
    parent: Task,
    labels: tuple[str, ...],
    depth: int,
    children: tuple[DecompositionResult, ...] = (),
) -> DecompositionResult:
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(str(parent.id)),
            subtasks=tuple(_subtask(label) for label in labels),
            task_structure=TaskStructure.PARALLEL,
        ),
        created_tasks=tuple(_child_task(label, parent=parent) for label in labels),
        depth=depth,
        children=children,
    )


def _three_level_tree() -> DecompositionResult:
    """engine and ui at the root, board and rotation under engine, grid under board."""
    objective = _objective()
    engine = _child_task("engine", parent=objective)
    board = _child_task("board", parent=engine)
    grid = _node(parent=board, labels=("grid",), depth=2)
    under_engine = _node(
        parent=engine, labels=("board", "rotation"), depth=1, children=(grid,)
    )
    return _node(
        parent=objective, labels=("engine", "ui"), depth=0, children=(under_engine,)
    )


def _provenance() -> PlanProvenance:
    return PlanProvenance(
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(str(as_uuid("objective"))),
        created_at=_CREATED_AT,
        status=PlanStatus.PENDING_REVIEW,
    )


def _plan_of(items: tuple[PlanItem, ...]) -> Plan:
    return Plan(
        id=as_uuid("plan"),
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(str(as_uuid("objective"))),
        items=items,
        task_structure=TaskStructure.PARALLEL,
        status=PlanStatus.APPROVED,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestProjectionDown:
    def test_a_flat_result_projects_flat(self) -> None:
        result = _node(parent=_objective(), labels=("engine", "ui"), depth=0)
        items = items_from_decomposition(result)
        assert [item.id for item in items] == [sid("engine"), sid("ui")]
        assert all(item.parent_id is None for item in items)

    def test_every_level_reaches_the_plan(self) -> None:
        items = items_from_decomposition(_three_level_tree())
        assert {item.id for item in items} == {
            sid("engine"),
            sid("ui"),
            sid("board"),
            sid("rotation"),
            sid("grid"),
        }

    def test_parent_links_reproduce_the_tree(self) -> None:
        items = items_from_decomposition(_three_level_tree())
        parents = {item.id: item.parent_id for item in items}
        assert parents[sid("engine")] is None
        assert parents[sid("ui")] is None
        assert parents[sid("board")] == sid("engine")
        assert parents[sid("rotation")] == sid("engine")
        assert parents[sid("grid")] == sid("board")

    def test_workstreams_come_first(self) -> None:
        items = items_from_decomposition(_three_level_tree())
        assert [item.id for item in items[:2]] == [sid("engine"), sid("ui")]

    def test_the_projected_plan_validates(self) -> None:
        plan = plan_from_decomposition(_three_level_tree(), _provenance())
        assert PlanTree.of(plan.items).depth(sid("grid")) == 2


class TestProjectionUp:
    def test_rebuilds_a_nested_result(self) -> None:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        assert rebuilt.max_depth_reached == 2
        assert {str(task.id) for task in rebuilt.all_tasks} == {
            sid("engine"),
            sid("ui"),
            sid("board"),
            sid("rotation"),
            sid("grid"),
        }

    def test_leaf_tasks_exclude_the_containers(self) -> None:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        assert {str(task.id) for task in rebuilt.leaf_tasks} == {
            sid("ui"),
            sid("rotation"),
            sid("grid"),
        }

    def test_a_nested_task_hangs_off_its_container(self) -> None:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        parents = {str(task.id): task.parent_task_id for task in rebuilt.all_tasks}
        assert parents[sid("engine")] == str(as_uuid("objective"))
        assert parents[sid("board")] == sid("engine")
        assert parents[sid("grid")] == sid("board")

    def test_round_trips_the_tree_shape(self) -> None:
        original = _three_level_tree()
        plan = _plan_of(items_from_decomposition(original))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        again = items_from_decomposition(rebuilt)
        assert {item.id: item.parent_id for item in again} == {
            item.id: item.parent_id for item in items_from_decomposition(original)
        }

    def test_an_operator_reparenting_an_item_is_what_builds(self) -> None:
        items = items_from_decomposition(_three_level_tree())
        edited = tuple(
            item.model_copy(update={"parent_id": NotBlankStr(sid("ui"))})
            if item.id == sid("grid")
            else item
            for item in items
        )
        rebuilt = decomposition_from_plan(_plan_of(edited), parent_task=_objective())
        parents = {str(task.id): task.parent_task_id for task in rebuilt.all_tasks}
        assert parents[sid("grid")] == sid("ui")


class TestContainerAssembly:
    def _tasks(self) -> dict[str, Task]:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        return {str(task.id): task for task in rebuilt.all_tasks}

    def test_a_container_is_briefed_to_assemble_its_own_children(self) -> None:
        engine = self._tasks()[sid("engine")]
        assert "Assemble the delivered work" in str(engine.description)
        assert "Unit board" in str(engine.description)
        assert "Unit rotation" in str(engine.description)

    def test_a_container_is_not_briefed_with_the_whole_tree(self) -> None:
        engine = self._tasks()[sid("engine")]
        assert "Unit ui" not in str(engine.description)
        assert "Unit grid" not in str(engine.description)

    def test_a_leaf_keeps_the_planner_description(self) -> None:
        rotation = self._tasks()[sid("rotation")]
        assert "Build rotation" in str(rotation.description)
        assert "Assemble the delivered work" not in str(rotation.description)

    def test_a_container_declares_its_own_and_the_assembly_evidence(self) -> None:
        paths = {str(a.path) for a in self._tasks()[sid("engine")].artifacts_expected}
        assert "src/engine.py" in paths
        assert any(p.endswith("/report.md") for p in paths)
        assert any(p.endswith("/end-to-end.txt") for p in paths)

    def test_sibling_containers_do_not_share_evidence_paths(self) -> None:
        tasks = self._tasks()
        engine = {str(a.path) for a in tasks[sid("engine")].artifacts_expected}
        board = {str(a.path) for a in tasks[sid("board")].artifacts_expected}
        assert not (engine & board)

    def test_a_container_runs_above_the_stakes_of_what_it_assembles(self) -> None:
        tasks = self._tasks()
        assert tasks[sid("board")].stakes is Stakes.HIGH
        assert tasks[sid("rotation")].stakes is Stakes.NORMAL

    def test_a_container_still_implements_its_own_plan_item(self) -> None:
        # plan_item_id set is what makes the rollup read the assembly as that
        # item's progress; the ROOT integration task is the one that leaves it
        # unset, and this must not become a second such task.
        engine = self._tasks()[sid("engine")]
        assert engine.plan_item_id is not None
        assert str(engine.plan_item_id) == sid("engine")


class TestDispatchSubtasks:
    def test_a_container_waits_on_its_children(self) -> None:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        by_id = {s.id: s for s in rebuilt.dispatch_subtasks}
        assert set(by_id[sid("engine")].dependencies) == {
            sid("board"),
            sid("rotation"),
        }
        assert by_id[sid("board")].dependencies == (sid("grid"),)
        assert by_id[sid("ui")].dependencies == ()

    def test_covers_every_node(self) -> None:
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        assert len(rebuilt.dispatch_subtasks) == len(rebuilt.all_tasks)

    def test_leaves_the_persisted_edges_alone(self) -> None:
        # parent_id owns structure and dependencies owns order. The augmented
        # view exists only inside dispatch, so what is persisted must be
        # byte-identical with and without it.
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        _ = rebuilt.dispatch_subtasks
        assert all(item.dependencies == () for item in plan.items)
        assert all(task.dependencies == () for task in rebuilt.all_tasks)
        assert all(s.dependencies == () for s in rebuilt.plan.subtasks)

    def test_a_declared_edge_survives_the_augmentation(self) -> None:
        items = items_from_decomposition(_three_level_tree())
        edited = tuple(
            item.model_copy(update={"dependencies": (NotBlankStr(sid("ui")),)})
            if item.id == sid("engine")
            else item
            for item in items
        )
        rebuilt = decomposition_from_plan(_plan_of(edited), parent_task=_objective())
        by_id = {s.id: s for s in rebuilt.dispatch_subtasks}
        assert set(by_id[sid("engine")].dependencies) == {
            sid("ui"),
            sid("board"),
            sid("rotation"),
        }

    def test_a_flat_result_is_unchanged_by_the_view(self) -> None:
        result = _node(parent=_objective(), labels=("engine", "ui"), depth=0)
        assert result.dispatch_subtasks == result.plan.subtasks


class TestWhatTheWaveBuilderReads:
    """The consequence of the augmented view, at the seam that consumes it.

    ``build_execution_waves`` reconstructs a ``DependencyGraph`` from
    ``dispatch_subtasks`` and calls ``parallel_groups()``, so the wave order a
    dispatcher runs is exactly this. Asserted here rather than through the
    builder because routing and config decide who runs each unit, and neither
    changes when.
    """

    def _levels(self) -> list[set[str]]:
        """The dispatch DAG's waves, each as a set of subtask ids.

        Returns:
            One entry per topological level, in execution order.
        """
        plan = _plan_of(items_from_decomposition(_three_level_tree()))
        rebuilt = decomposition_from_plan(plan, parent_task=_objective())
        graph = DependencyGraph(rebuilt.dispatch_subtasks)
        return [set(group) for group in graph.parallel_groups()]

    def test_a_container_runs_strictly_after_what_it_assembles(self) -> None:
        levels = self._levels()
        position = {
            subtask_id: index
            for index, level in enumerate(levels)
            for subtask_id in level
        }
        assert position[sid("grid")] < position[sid("board")]
        assert position[sid("board")] < position[sid("engine")]

    def test_independent_subtrees_still_run_together(self) -> None:
        # A per-subtree loop walked deepest-first would serialise these. The
        # one global computation is what keeps cross-subtree parallelism.
        levels = self._levels()
        assert {sid("grid"), sid("ui")} <= levels[0]

    def test_every_unit_of_the_tree_is_scheduled(self) -> None:
        # A unit in no wave is one no dispatcher runs, no gate parks and no
        # rollup can conclude on.
        scheduled = {subtask_id for level in self._levels() for subtask_id in level}
        assert scheduled == {
            sid(label) for label in ("engine", "ui", "board", "rotation", "grid")
        }
