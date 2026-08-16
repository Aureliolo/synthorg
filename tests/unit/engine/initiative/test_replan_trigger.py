"""Tests for the stalled-initiative auto-replan trigger."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.replan_trigger import ACTOR, ReplanTriggerService
from synthorg.engine.task_engine import TaskEngine
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_PARENT = sid("parent-1")
_ITEM_A = sid("item-a")
_ITEM_B = sid("item-b")


class _RecordingReplan:
    """An :class:`InitiativeReplanPort` that records what it was asked to open."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, tuple[PlanItem, ...], str, int]] = []

    async def replan(
        self,
        existing: Plan,
        *,
        items: tuple[PlanItem, ...],
        requested_by: str,
        replan_generation: int,
    ) -> Plan:
        self.calls.append((existing.id, items, requested_by, replan_generation))
        return existing.model_copy(
            update={
                "id": as_uuid("plan-successor"),
                "items": items,
                "status": PlanStatus.PENDING_REVIEW,
                "replan_generation": replan_generation,
            }
        )


def _item(item_id: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
    )


def _plan(
    *items: PlanItem,
    status: PlanStatus = PlanStatus.EXECUTING,
    generation: int = 0,
) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(_PARENT),
        items=items,
        status=status,
        objective_criteria=(NotBlankStr("the game is playable"),),
        replan_generation=generation,
        created_at=now,
        updated_at=now,
    )


def _task(item_id: str, status: TaskStatus) -> Task:
    return Task(
        id=UUID(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=UUID(item_id),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


def _objective() -> Task:
    return Task(
        id=UUID(_PARENT),
        title="Objective",
        description="Ship the game",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        created_by="ceo",
        assigned_to=sid("coordinator"),
        status=TaskStatus.IN_PROGRESS,
    )


def _decomposition(*subtask_ids: str) -> DecompositionResult:
    subtasks = tuple(
        SubtaskDefinition(
            id=sid(subtask_id),
            title=f"Revised {subtask_id}",
            description="Another way at it",
            acceptance_criteria=(NotBlankStr("it works"),),
            expected_artifacts=(NotBlankStr("src/revised.py"),),
        )
        for subtask_id in subtask_ids
    )
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=_PARENT,
            subtasks=subtasks,
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        created_tasks=tuple(
            Task(
                id=UUID(subtask.id),
                title=subtask.title,
                description=subtask.description,
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project=sid(_PROJECT),
                created_by="manager",
                parent_task_id=_PARENT,
            )
            for subtask in subtasks
        ),
    )


async def _seed(
    plan: Plan,
    *tasks: Task,
    decomposition: DecompositionResult | None = None,
    objective_missing: bool = False,
    settings: dict[str, object] | None = None,
) -> tuple[ReplanTriggerService, _RecordingReplan, AsyncMock]:
    """Build the trigger over a seeded backend.

    Returns:
        The service, the recording replan port, and the decomposition mock.
    """
    objective = None if objective_missing else _objective()
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    for task in tasks:
        await backend.tasks.save(task)
    decompose = AsyncMock(return_value=decomposition or _decomposition("sub-1"))
    replan = _RecordingReplan()
    values = settings or {}
    service = ReplanTriggerService(
        persistence=backend,
        task_engine=mock_of[TaskEngine](
            get_task=AsyncMock(return_value=objective),
        ),
        decomposition_service=mock_of[DecompositionService](decompose_task=decompose),
        replan=replan,
        config_resolver=mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=values.get("enabled", True)),
            get_int=AsyncMock(return_value=values.get("max_generations", 2)),
            get_float=AsyncMock(return_value=values.get("timeout", 30.0)),
        ),
        clock=FakeClock(),
    )
    return service, replan, decompose


async def _fire(
    service: ReplanTriggerService,
    plan: Plan,
    reason: StallReason = StallReason.ALL_FAILED,
    detail: str | None = None,
) -> None:
    """Schedule a replan and wait for the detached task to finish."""
    service.schedule(plan=plan, reason=reason, detail=detail)
    await service.drain(timeout_sec=5.0)


class TestTailStageVerdicts:
    """A verdict no derivation over items can see."""

    async def test_an_integration_failure_replans_a_plan_still_integrating(
        self,
    ) -> None:
        """Every item is COMPLETED here, so `stall_reason` sees nothing."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING)
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.COMPLETED))

        await _fire(service, plan, StallReason.INTEGRATION_FAILED)

        assert len(replan.calls) == 1

    async def test_an_unmet_evaluation_replans_a_plan_still_evaluating(self) -> None:
        plan = _plan(_item(_ITEM_A), status=PlanStatus.EVALUATING)
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.COMPLETED))

        await _fire(service, plan, StallReason.EVALUATION_UNMET)

        assert len(replan.calls) == 1

    async def test_a_verdict_from_a_stage_the_plan_has_left_is_dropped(self) -> None:
        """It was already dealt with: a human replanned it, or the stage re-ran."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.EVALUATING)
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.COMPLETED))

        await _fire(service, plan, StallReason.INTEGRATION_FAILED)

        assert replan.calls == []

    async def test_the_stage_detail_reaches_the_successors_planner(self) -> None:
        """The judged evidence is the only account of what actually failed."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.EVALUATING)
        service, _, decompose = await _seed(plan, _task(_ITEM_A, TaskStatus.COMPLETED))

        await _fire(
            service,
            plan,
            StallReason.EVALUATION_UNMET,
            detail="- it saves scores [unmet]: the file is never written",
        )

        assert decompose.await_args is not None
        briefed = decompose.await_args.args[0]
        assert "the file is never written" in briefed.description


class TestReplan:
    """The happy path: a dead plan is replanned into a successor."""

    async def test_a_stalled_plan_opens_a_successor(self) -> None:
        plan = _plan(_item(_ITEM_A), _item(_ITEM_B))
        service, replan, _ = await _seed(
            plan,
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.FAILED),
        )

        await _fire(service, plan)

        assert len(replan.calls) == 1
        plan_id, items, requested_by, generation = replan.calls[0]
        assert plan_id == plan.id
        assert [item.title for item in items] == ["Revised sub-1"]
        assert requested_by == ACTOR
        assert generation == 1

    async def test_the_brief_carries_the_stall_into_the_decomposition(self) -> None:
        """A successor planned without the stall would repeat it."""
        plan = _plan(_item(_ITEM_A), _item(_ITEM_B))
        service, _, decompose = await _seed(
            plan,
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.BLOCKED),
        )

        await _fire(service, plan)

        assert decompose.await_args is not None
        briefed = decompose.await_args.args[0]
        assert StallReason.BLOCKED.value in briefed.description
        assert "OUTSTANDING (blocked)" in briefed.description
        assert "DELIVERED" in briefed.description
        # The objective itself must not accumulate briefs across replans.
        assert "Ship the game" in briefed.description

    async def test_a_generation_carries_forward(self) -> None:
        plan = _plan(_item(_ITEM_A), generation=1)
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.FAILED))

        await _fire(service, plan)

        assert replan.calls[0][3] == 2


class TestGuards:
    """Every reason the trigger declines to replan."""

    async def test_a_recovered_plan_is_not_replanned(self) -> None:
        """The verdict was derived before the task ran; the world moved on."""
        plan = _plan(_item(_ITEM_A), _item(_ITEM_B))
        service, replan, _ = await _seed(
            plan,
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.IN_PROGRESS),
        )

        await _fire(service, plan)

        assert replan.calls == []

    async def test_a_superseded_plan_is_not_replanned(self) -> None:
        """What makes a redelivered rollup event harmless."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SUPERSEDED)
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.FAILED))

        await _fire(service, plan)

        assert replan.calls == []

    async def test_the_generation_cap_parks_the_initiative(self) -> None:
        plan = _plan(_item(_ITEM_A), generation=2)
        service, replan, _ = await _seed(
            plan,
            _task(_ITEM_A, TaskStatus.FAILED),
            settings={"max_generations": 2},
        )

        await _fire(service, plan)

        assert replan.calls == []

    async def test_the_kill_switch_stops_the_trigger(self) -> None:
        plan = _plan(_item(_ITEM_A))
        service, replan, _ = await _seed(
            plan,
            _task(_ITEM_A, TaskStatus.FAILED),
            settings={"enabled": False},
        )

        await _fire(service, plan)

        assert replan.calls == []

    async def test_a_missing_objective_task_is_not_replanned(self) -> None:
        """The successor is planned from the objective; without it there is none."""
        plan = _plan(_item(_ITEM_A))
        service, replan, _ = await _seed(
            plan, _task(_ITEM_A, TaskStatus.FAILED), objective_missing=True
        )

        await _fire(service, plan)

        assert replan.calls == []

    async def test_a_second_schedule_while_one_is_in_flight_is_dropped(self) -> None:
        """The rollup fires on every stalled recompute, not on an edge."""
        plan = _plan(_item(_ITEM_A))
        service, replan, _ = await _seed(plan, _task(_ITEM_A, TaskStatus.FAILED))

        service.schedule(plan=plan, reason=StallReason.ALL_FAILED)
        service.schedule(plan=plan, reason=StallReason.ALL_FAILED)
        await service.drain(timeout_sec=5.0)

        assert len(replan.calls) == 1

    async def test_a_decomposition_failure_never_escapes(self) -> None:
        """The trigger runs on a best-effort tail; the stall simply persists."""
        plan = _plan(_item(_ITEM_A))
        service, replan, decompose = await _seed(
            plan, _task(_ITEM_A, TaskStatus.FAILED)
        )
        decompose.side_effect = RuntimeError("planner unavailable")

        await _fire(service, plan)

        assert replan.calls == []
