"""Initiative rollup driven through the real ``TaskEngine`` observer seam.

The unit tests call ``recompute`` directly. This exercises the wiring the
product actually runs on: the service registered as a ``TaskEngine`` observer,
a task transitioned through the engine's own mutation path, and the plan and
project advancing off the resulting event.

The negative case is the one that matters: a task that has executed but is
still awaiting verification must not complete the initiative.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    PlanReconcilePort,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.tail_stages import integration_task_id
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from tests._shared import FakeClock, as_uuid, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend
from tests.unit.engine.task_engine_helpers import (
    FakeMessageBus as EngineMessageBus,
)

pytestmark = pytest.mark.integration

_PROJECT = "proj-rollup"
_PLAN = "plan-rollup"
_ITEM_A = "11111111-1111-5111-8111-111111111111"
_ITEM_B = "22222222-2222-5222-8222-222222222222"


def _item(item_id: str, title: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(title),
        description=NotBlankStr("Do the work"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/work.py"),),
    )


def _child(item_id: str, status: TaskStatus) -> Task:
    return Task(
        id=UUID(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN),
        plan_item_id=UUID(item_id),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


async def _wired(
    *statuses: tuple[str, TaskStatus],
    integration: IntegrationPort | None = None,
    evaluation: EvaluationPort | None = None,
) -> tuple[TaskEngine, FakePersistenceBackend]:
    """Start a TaskEngine with the rollup registered, seeded with an initiative.

    Returns:
        The started engine and the backing persistence.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    now = datetime(2026, 7, 19, tzinfo=UTC)
    await backend.plans.save(
        Plan(
            id=as_uuid(_PLAN),
            project=NotBlankStr(sid(_PROJECT)),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the initiative"),
            parent_task_id=NotBlankStr(sid("parent-1")),
            items=(_item(_ITEM_A, "Scaffold"), _item(_ITEM_B, "Build")),
            status=PlanStatus.EXECUTING,
            created_at=now,
            updated_at=now,
        )
    )
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN),
            status=ProjectStatus.ACTIVE,
        )
    )
    for item_id, status in statuses:
        await backend.tasks.save(_child(item_id, status))

    clock = FakeClock()
    engine = TaskEngine(
        config=TaskEngineConfig(),
        persistence=backend,
        message_bus=EngineMessageBus(),  # type: ignore[arg-type]
    )
    rollup = ProjectRollupService(
        persistence=backend,
        plan_status_writer=build_plan_service(backend, clock=clock),
        clock=clock,
        integration=integration,
        evaluation=evaluation,
    )
    engine.register_observer(rollup.on_task_state_changed)
    await engine.start()
    return engine, backend


class _MintingIntegration:
    """An INTEGRATE stage that mints the assembly job, without a work spine.

    Stands in for ``IntegrationStageService`` at the one point that matters to
    the rollup: the task exists, carries the deterministic id, and its status
    is what the tail gate reads.
    """

    def __init__(self) -> None:
        self.fired = 0

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        del plan, attempt
        self.fired += 1

    async def drain(self, *, timeout_sec: float) -> None:
        del timeout_sec

    async def pass_the_job(self, backend: FakePersistenceBackend) -> None:
        """Persist the assembly job as having passed its review gate."""
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN)))
        assert plan is not None
        await backend.tasks.save(
            Task(
                id=UUID(integration_task_id(plan, 0)),
                title="Integrate",
                description="Assemble the pieces",
                type=TaskType.DEVELOPMENT,
                priority=Priority.HIGH,
                project=sid(_PROJECT),
                plan_id=as_uuid(_PLAN),
                created_by="initiative-integrate",
                assigned_to=sid("agent-1"),
                status=TaskStatus.COMPLETED,
            )
        )


class _PassingEvaluation:
    """An EVALUATE stage whose verdict is that the objective is met.

    Mirrors the real stage's shape in the two ways that matter here: the
    rollup only fires it, the stage itself writes the one transition that
    completes a plan, and it then calls back into the rollup. That callback is
    load-bearing rather than decorative: the completion write mutates no task,
    so it emits no observer event, and without it the project and the
    objective task would stay a stage behind the plan forever.
    """

    def __init__(self) -> None:
        self.fired = 0
        self._reconcile: PlanReconcilePort | None = None

    def bind(self, reconcile: PlanReconcilePort) -> None:
        """Attach the rollup this stage reports back to."""
        self._reconcile = reconcile

    def schedule(self, *, plan: Plan) -> None:
        del plan
        self.fired += 1

    async def drain(self, *, timeout_sec: float) -> None:
        del timeout_sec

    async def deliver(self, backend: FakePersistenceBackend) -> None:
        """Write the passing verdict's completion, then reconcile the graph."""
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN)))
        assert plan is not None
        await build_plan_service(backend, clock=FakeClock()).sync_status(
            plan,
            PlanStatus.COMPLETED,
            requested_by="initiative-evaluate",
            reason="evaluation: every success criterion met",
        )
        assert self._reconcile is not None
        await self._reconcile.recompute(plan.id)


def _rollup(
    backend: FakePersistenceBackend,
    *,
    integration: IntegrationPort | None = None,
    evaluation: EvaluationPort | None = None,
) -> ProjectRollupService:
    """Build a rollup over *backend* outside the engine's observer path.

    Returns:
        The rollup service, for driving a recompute directly.
    """
    clock = FakeClock()
    return ProjectRollupService(
        persistence=backend,
        plan_status_writer=build_plan_service(backend, clock=clock),
        clock=clock,
        integration=integration,
        evaluation=evaluation,
    )


async def _statuses(
    backend: FakePersistenceBackend,
) -> tuple[PlanStatus, ProjectStatus]:
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN)))
    project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
    assert plan is not None
    assert project is not None
    return plan.status, project.status


class TestInitiativeRollupWiring:
    async def test_last_task_passing_opens_the_tail(self) -> None:
        """Every item verified opens INTEGRATING, and never completes.

        The tail's stages are unwired in this boot, so the initiative parks
        where an unassembled, unscored initiative honestly belongs rather than
        being declared delivered.
        """
        engine, backend = await _wired(
            (_ITEM_A, TaskStatus.COMPLETED),
            (_ITEM_B, TaskStatus.IN_REVIEW),
        )
        try:
            # The review gate's decision: the final item passes verification.
            await engine.transition_task(
                _ITEM_B,
                TaskStatus.COMPLETED,
                requested_by="reviewer",
            )
        finally:
            # Stop drains the observer queue, so the rollup has run by the
            # time the assertions below read the statuses.
            await engine.stop()

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )

    async def test_the_wired_tail_carries_the_initiative_to_delivery(self) -> None:
        """The composition end to end: build, assemble, score, deliver.

        Each stage is driven by the same observer events the product runs on,
        and each hop needs the previous stage's own verdict: the plan cannot
        reach COMPLETED without an assembly job that passed the review gate
        and an evaluation that judged the objective met.
        """
        integration = _MintingIntegration()
        evaluation = _PassingEvaluation()
        engine, backend = await _wired(
            (_ITEM_A, TaskStatus.COMPLETED),
            (_ITEM_B, TaskStatus.IN_REVIEW),
            integration=integration,
            evaluation=evaluation,
        )
        try:
            await engine.transition_task(
                _ITEM_B, TaskStatus.COMPLETED, requested_by="reviewer"
            )
        finally:
            # Stop drains the observer queue, so the rollup has run by the
            # time the assertions below read the statuses.
            await engine.stop()

        # The tail opened and the assembly job was minted.
        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )
        assert integration.fired == 1

        # The assembly job passes its own review gate.
        await integration.pass_the_job(backend)
        rollup = _rollup(backend, integration=integration, evaluation=evaluation)
        evaluation.bind(rollup)
        await rollup.recompute(as_uuid(_PLAN))

        # Which opens evaluation, whose verdict delivers the initiative. No
        # manual recompute here: the stage's own callback is what carries the
        # project and the objective task across with the plan.
        assert evaluation.fired == 1
        await evaluation.deliver(backend)
        assert await _statuses(backend) == (
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
        )

    async def test_unverified_task_cannot_complete_the_initiative(self) -> None:
        """Executed but not yet verified must not count as delivered."""
        engine, backend = await _wired(
            (_ITEM_A, TaskStatus.COMPLETED),
            (_ITEM_B, TaskStatus.IN_PROGRESS),
        )
        try:
            # The execution loop hands the task to review; the completion
            # oracle has not ruled on it yet.
            await engine.transition_task(
                _ITEM_B,
                TaskStatus.IN_REVIEW,
                requested_by="execution",
            )
        finally:
            # Drains the observer queue before the assertions; see above.
            await engine.stop()

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )
