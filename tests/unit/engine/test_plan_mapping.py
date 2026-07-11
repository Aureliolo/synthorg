"""Tests for the ``DecompositionResult`` -> ``Plan`` projection."""

from datetime import UTC, datetime

import pytest

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
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="Movement",
                description="Drop + rotate",
                dependencies=(sid("sub-1"),),
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
    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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
            ),
            PlanItem(
                id=NotBlankStr(str(as_uuid("sub-2"))),
                title=NotBlankStr("Movement"),
                description=NotBlankStr("Drop"),
                dependencies=(NotBlankStr(str(as_uuid("sub-1"))),),
                owner=NotBlankStr("engineering"),
            ),
        ),
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestDecompositionFromPlan:
    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_routing_hints_survive_round_trip(self) -> None:
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())
        by_id = {s.id: s for s in result.plan.subtasks}
        assert by_id[str(as_uuid("sub-1"))].required_skills == ("frontend",)
        assert by_id[str(as_uuid("sub-2"))].required_role == "engineering"
