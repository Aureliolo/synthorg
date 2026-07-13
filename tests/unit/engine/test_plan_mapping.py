"""Tests for the ``DecompositionResult`` -> ``Plan`` projection."""

from datetime import UTC, datetime

import pytest

from synthorg.core.artifact import ArtifactType
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    Stakes,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.plan_mapping import (
    decomposition_from_plan,
    plan_from_decomposition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _result_task(subtask_id: str) -> Task:
    return Task(
        id=as_uuid(subtask_id),
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _decomposition() -> DecompositionResult:
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="Board",
                description="Grid + rendering",
                estimated_complexity=Complexity.COMPLEX,
                stakes=Stakes.HIGH,
                required_role="engineering",
                acceptance_criteria=(NotBlankStr("board renders"),),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="Movement",
                description="Drop + rotate",
                dependencies=(sid("sub-1"),),
                acceptance_criteria=(NotBlankStr("pieces move"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        coordination_topology=CoordinationTopology.AUTO,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(_result_task("sub-1"), _result_task("sub-2")),
    )


class TestPlanFromDecomposition:
    def test_maps_structure_and_items(self) -> None:
        plan = plan_from_decomposition(
            _decomposition(),
            project="beachhead",
            objective_id="obj-1",
            parent_task_id=sid("root"),
            created_at=_CREATED_AT,
        )

        assert plan.project == "beachhead"
        assert plan.objective_id == "obj-1"
        assert plan.parent_task_id == sid("root")
        assert plan.status is PlanStatus.PENDING_REVIEW
        assert plan.task_structure is TaskStructure.SEQUENTIAL
        assert plan.coordination_topology is CoordinationTopology.AUTO
        assert plan.created_at == _CREATED_AT
        assert plan.updated_at == _CREATED_AT
        assert plan.version == 1

    def test_item_fields_projected(self) -> None:
        plan = plan_from_decomposition(
            _decomposition(),
            project="beachhead",
            objective_id="obj-1",
            parent_task_id=sid("root"),
            created_at=_CREATED_AT,
        )

        first, second = plan.items
        assert first.id == sid("sub-1")
        assert first.owner == "engineering"
        assert first.estimated_complexity is Complexity.COMPLEX
        assert first.stakes is Stakes.HIGH
        assert second.dependencies == (sid("sub-1"),)
        assert second.owner is None

    def test_status_override(self) -> None:
        plan = plan_from_decomposition(
            _decomposition(),
            project="beachhead",
            objective_id="obj-1",
            parent_task_id=sid("root"),
            created_at=_CREATED_AT,
            status=PlanStatus.DRAFT,
        )
        assert plan.status is PlanStatus.DRAFT

    def test_artifacts_and_criteria_projected_from_subtask(self) -> None:
        # The subtask-level expected_artifacts + acceptance_criteria must land
        # on the plan item so the durable plan (and every task built from it)
        # arms the fail-loud zero-artifact guard.
        decomposition = DecompositionResult(
            plan=DecompositionPlan(
                parent_task_id=sid("root"),
                subtasks=(
                    SubtaskDefinition(
                        id=sid("sub-1"),
                        title="Board",
                        description="Grid + rendering",
                        expected_artifacts=(
                            NotBlankStr("src/board.tsx"),
                            NotBlankStr("tests/board.test.tsx"),
                        ),
                        acceptance_criteria=(NotBlankStr("renders a 10x20 grid"),),
                    ),
                ),
            ),
            created_tasks=(_result_task("sub-1"),),
        )
        plan = plan_from_decomposition(
            decomposition,
            project="beachhead",
            objective_id="obj-1",
            parent_task_id=sid("root"),
            created_at=_CREATED_AT,
        )
        item = plan.items[0]
        assert item.expected_artifacts == ("src/board.tsx", "tests/board.test.tsx")
        assert item.acceptance_criteria == ("renders a 10x20 grid",)
        # And the reverse projection round-trips them back onto the subtask.
        rebuilt = decomposition_from_plan(plan, parent_task=_parent_task())
        subtask = rebuilt.plan.subtasks[0]
        assert subtask.expected_artifacts == ("src/board.tsx", "tests/board.test.tsx")
        assert subtask.acceptance_criteria == ("renders a 10x20 grid",)


def _parent_task() -> Task:
    return Task(
        id=as_uuid("root"),
        title="Objective",
        description="Ship the game",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _durable_plan() -> Plan:
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj-1"),
        parent_task_id=NotBlankStr(str(as_uuid("root"))),
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("sub-1"))),
                title=NotBlankStr("Board"),
                description=NotBlankStr("Grid"),
                required_skills=(NotBlankStr("frontend"),),
                acceptance_criteria=(NotBlankStr("board renders"),),
            ),
            PlanItem(
                id=NotBlankStr(str(as_uuid("sub-2"))),
                title=NotBlankStr("Movement"),
                description=NotBlankStr("Drop"),
                dependencies=(NotBlankStr(str(as_uuid("sub-1"))),),
                owner=NotBlankStr("engineering"),
                acceptance_criteria=(NotBlankStr("pieces move"),),
            ),
        ),
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestDecompositionFromPlan:
    def test_rebuilds_dispatchable_result(self) -> None:
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())

        assert result.plan.parent_task_id == str(as_uuid("root"))
        assert result.plan.task_structure is TaskStructure.PARALLEL
        assert {s.id for s in result.plan.subtasks} == {
            str(as_uuid("sub-1")),
            str(as_uuid("sub-2")),
        }
        # Child tasks are fresh CREATED work with ids derived from item ids.
        assert {str(t.id) for t in result.created_tasks} == {
            str(as_uuid("sub-1")),
            str(as_uuid("sub-2")),
        }
        assert all(
            str(t.parent_task_id) == str(as_uuid("root")) for t in result.created_tasks
        )
        assert result.dependency_edges == (
            (str(as_uuid("sub-1")), str(as_uuid("sub-2"))),
        )

    def test_routing_hints_survive_round_trip(self) -> None:
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())
        by_id = {s.id: s for s in result.plan.subtasks}
        assert by_id[str(as_uuid("sub-1"))].required_skills == ("frontend",)
        assert by_id[str(as_uuid("sub-2"))].required_role == "engineering"

    def test_plan_from_decomposition_round_trips(self) -> None:
        # A plan built from a decomposition, then projected back, preserves the
        # item identity, dependency edges, and structure (the mapping's contract).
        plan = plan_from_decomposition(
            _decomposition(),
            project="beachhead",
            objective_id="obj-1",
            parent_task_id=sid("root"),
            created_at=_CREATED_AT,
        )
        rebuilt = decomposition_from_plan(plan, parent_task=_parent_task())
        assert {s.id for s in rebuilt.plan.subtasks} == {item.id for item in plan.items}
        assert rebuilt.plan.task_structure is plan.task_structure
        assert rebuilt.dependency_edges == ((sid("sub-1"), sid("sub-2")),)

    def test_expected_artifacts_and_criteria_reach_the_task(self) -> None:
        # The item's acceptance criteria + expected artifacts must land on the
        # dispatched Task so the fail-loud zero-artifact guard can engage.
        plan = Plan(
            id=as_uuid("plan-2"),
            project=NotBlankStr("beachhead"),
            objective_id=NotBlankStr("obj-1"),
            parent_task_id=NotBlankStr(str(as_uuid("root"))),
            items=(
                PlanItem(
                    id=NotBlankStr(str(as_uuid("sub-1"))),
                    title=NotBlankStr("Board"),
                    description=NotBlankStr("Grid"),
                    acceptance_criteria=(NotBlankStr("renders an 8x8 grid"),),
                    expected_artifacts=(
                        NotBlankStr("src/board.py"),
                        NotBlankStr("tests/test_board.py"),
                    ),
                ),
            ),
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
        result = decomposition_from_plan(plan, parent_task=_parent_task())
        task = result.created_tasks[0]
        assert tuple(c.description for c in task.acceptance_criteria) == (
            "renders an 8x8 grid",
        )
        paths = {a.path for a in task.artifacts_expected}
        assert paths == {"src/board.py", "tests/test_board.py"}
        # The test path is typed TESTS, the source path CODE (inferred).
        by_path = {a.path: a.type for a in task.artifacts_expected}
        assert by_path["tests/test_board.py"] is ArtifactType.TESTS
        assert by_path["src/board.py"] is ArtifactType.CODE
