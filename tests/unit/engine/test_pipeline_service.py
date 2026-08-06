"""Unit tests for the default work pipeline service."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    QueryError,
)
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionSubtaskLimitError,
    ProjectNotFoundError,
)
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineTeamPathUnavailableError,
    WorkRoutingUndecidableError,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    PlanReviewHandoff,
    RoutingVerdict,
    WorkItem,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.routing.models import RoutingCandidate
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.workers.execution_service import WorkerExecutionService
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit

_MIN_SCORE = 0.1
# The low-confidence threshold is a property on the real scorer (autospec makes
# it a mock), so a test reaching solo selection sets it explicitly. Matches the
# scorer's default; candidates above it take the normal (not-low) path.
_LOW_CONFIDENCE_SCORE = 0.35


def _work_item(**overrides: object) -> WorkItem:
    base: dict[str, object] = {
        "origin_adapter_id": "harness",
        "source": WorkSource.SIMULATION,
        "title": "Add health endpoint",
        "raw_intent": "Return 200 with a JSON status body.",
        "project": "proj-1",
        "requested_by": "operator-1",
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return WorkItem.model_validate(base)


def _task(
    status: TaskStatus = TaskStatus.CREATED,
    *,
    assigned_to: str | None = None,
) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Add health endpoint",
        description="Return 200.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="operator-1",
        status=status,
        assigned_to=assigned_to,
    )


def _post_task(status: TaskStatus) -> Task:
    """A terminal-status task (requires an assignee)."""
    return _task(status, assigned_to="agent-1")


def _preview() -> DecompositionResult:
    """A minimal real decomposition the gated-plan path feeds to the panel."""
    plan = DecompositionPlan(
        parent_task_id=sid("task-1"),
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="Slice",
                description="Deliver the slice",
                expected_artifacts=("src/slice.py",),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            Task(
                id=as_uuid("sub-1"),
                title="Slice",
                description="Deliver the slice",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project="proj-1",
                created_by="operator-1",
            ),
        ),
    )


def _project(*, lead: str | None = None) -> Project:
    """A real (non-mock) project so owner staffing can read/stamp ``lead``."""
    return Project(
        id=as_uuid("proj-1"),
        name="Beachhead",
        lead=lead,
    )


def _pipeline(
    *,
    intake_result: IntakeResult,
    task: Task | None,
    project: Project | None,
    verdict: RoutingVerdict,
    coordinator: MultiAgentCoordinator | None,
    agents: tuple[AgentIdentity, ...],
    candidate_score: float = 0.9,
    post_task: Task | None = None,
) -> tuple[DefaultWorkPipeline, dict[str, object]]:
    identity = make_e2e_identity()
    intake_engine = mock_of[IntakeEngine]()
    intake_engine.process.return_value = (None, intake_result)
    task_engine = mock_of[TaskEngine]()
    task_engine.get_task.return_value = post_task or task
    project_repo = mock_of[ProjectRepository]()
    project_repo.get.return_value = project
    routing_policy = mock_of[WorkRoutingPolicy]()
    routing_policy.decide.return_value = verdict
    scorer = mock_of[AgentTaskScorer]()
    scorer.min_score = _MIN_SCORE
    scorer.score.return_value = RoutingCandidate(
        agent_identity=identity,
        score=candidate_score,
        reason="test",
    )
    worker = mock_of[WorkerExecutionService]()
    worker.execute_once.return_value = post_task or _post_task(TaskStatus.IN_REVIEW)
    registry = mock_of[AgentRegistryService]()
    registry.list_active.return_value = agents
    pipeline = DefaultWorkPipeline(
        intake_engine=intake_engine,
        task_engine=task_engine,
        project_repository=project_repo,
        routing_policy=routing_policy,
        scorer=scorer,
        worker_execution_service=worker,
        coordinator=coordinator,
        agent_registry=registry,
        clock=FakeClock(),
    )
    handles = {
        "identity": identity,
        "worker": worker,
        "task_engine": task_engine,
        "project_repo": project_repo,
        "registry": registry,
    }
    return pipeline, handles


class TestIntakePhase:
    async def test_rejected_intake_raises(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.rejected_result(
                request_id="corr-1", reason="too vague"
            ),
            task=None,
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
        )
        with pytest.raises(WorkIntakeRejectedError, match="too vague"):
            await pipeline.run(_work_item())

    async def test_accepted_but_task_missing_raises(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=None,
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
        )
        with pytest.raises(WorkIntakeRejectedError, match="not persisted"):
            await pipeline.run(_work_item())


class TestProjectPhase:
    async def test_missing_project_raises(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=None,
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
        )
        with pytest.raises(ProjectNotFoundError):
            await pipeline.run(_work_item())


class TestSoloPath:
    async def test_leaf_runs_solo_not_team(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
            post_task=_post_task(TaskStatus.IN_REVIEW),
        )
        result = await pipeline.run(_work_item())
        assert result.execution_path is ExecutionPath.SOLO
        assert result.verdict is RoutingVerdict.LEAF
        assert result.final_task_status is TaskStatus.IN_REVIEW
        assert result.is_success is True
        cast("AsyncMock", handles["worker"]).execute_once.assert_awaited_once()
        coordinator.coordinate.assert_not_called()

    async def test_no_agents_raises_undecidable(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(),
        )
        with pytest.raises(WorkRoutingUndecidableError):
            await pipeline.run(_work_item())

    async def test_no_agent_above_threshold_raises(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
            candidate_score=0.0,
        )
        with pytest.raises(WorkRoutingUndecidableError, match="threshold"):
            await pipeline.run(_work_item())


class TestTeamPath:
    async def test_splittable_runs_team_not_solo(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
            post_task=_post_task(TaskStatus.COMPLETED),
        )
        result = await pipeline.run(_work_item())
        assert result.execution_path is ExecutionPath.TEAM
        assert result.final_task_status is TaskStatus.COMPLETED
        coordinator.coordinate.assert_awaited_once()
        cast("AsyncMock", handles["worker"]).execute_once.assert_not_called()

    async def test_splittable_without_coordinator_raises(self) -> None:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=None,
            agents=(make_e2e_identity(),),
        )
        with pytest.raises(WorkPipelineTeamPathUnavailableError):
            await pipeline.run(_work_item())


class TestPlanReviewGate:
    async def test_splittable_gates_plan_when_gate_wired(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=3,
                    detail="3 subtasks awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        result = await pipeline.run(_work_item())

        assert result.execution_path is ExecutionPath.PLAN_REVIEW
        assert result.plan_review_handoff is not None
        assert result.plan_review_handoff.approval_id == "appr-1"
        assert result.plan_review_handoff.subtask_count == 3
        # The plan is decomposed for review, but NOT dispatched: nothing
        # builds until the human approves the parked plan.
        coordinator.plan_preview.assert_awaited_once()
        coordinator.coordinate.assert_not_called()

    async def test_decomposition_failure_marks_plan_failed_not_500(self) -> None:
        # The Tetris dogfood regression: a decomposition failure must degrade to
        # a visible FAILED plan + FAILED task + is_success=false, never a raw 500
        # that leaves a project + orphan task behind with no plan.
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.side_effect = DecompositionError("decompose boom")
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate]()
        pipeline.attach_plan_review_gate(gate)

        result = await pipeline.run(_work_item(plan_required=True))

        assert result.is_success is False
        assert result.final_task_status is TaskStatus.FAILED
        assert result.execution_path is ExecutionPath.PLAN_REVIEW
        assert result.plan_review_handoff is not None
        # No approval is parked: the shell is opened, then marked FAILED.
        assert result.plan_review_handoff.approval_id is None
        cast("AsyncMock", gate.open_plan).assert_awaited_once()
        cast("AsyncMock", gate.fail_plan).assert_awaited_once()
        # The failure reason threads through to the durable plan so Plan Review
        # shows WHY it failed, not just that it did.
        fail_call = cast("AsyncMock", gate.fail_plan).await_args
        assert fail_call is not None
        assert "decompose boom" in fail_call.kwargs["reason"]
        cast("AsyncMock", gate.request_plan_approval).assert_not_awaited()

    async def test_an_over_limit_plan_surfaces_on_the_plan_not_a_thinner_plan(
        self,
    ) -> None:
        # F5: the agent-session strategy used to swap the researched plan for
        # the single-shot fallback's. It now refuses, and the refusal lands on
        # the durable plan naming both numbers, so the operator can raise
        # max_subtasks or narrow the objective.
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.side_effect = DecompositionSubtaskLimitError(
            produced=14, limit=10
        )
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate]()
        pipeline.attach_plan_review_gate(gate)

        result = await pipeline.run(_work_item(plan_required=True))

        assert result.is_success is False
        assert result.final_task_status is TaskStatus.FAILED
        fail_call = cast("AsyncMock", gate.fail_plan).await_args
        assert fail_call is not None
        assert "14 subtasks" in fail_call.kwargs["reason"]
        assert "max_subtasks of 10" in fail_call.kwargs["reason"]

    async def test_approval_park_failure_marks_plan_failed_not_500(self) -> None:
        # A failure AFTER decomposition (parking the approval) is now also
        # compensated by the pipeline guard: FAILED plan + FAILED task +
        # is_success=false, never an escaping 500.
        coordinator = mock_of[MultiAgentCoordinator]()
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate]()
        cast("AsyncMock", gate.request_plan_approval).side_effect = QueryError(
            "park boom"
        )
        pipeline.attach_plan_review_gate(gate)

        result = await pipeline.run(_work_item(plan_required=True))

        assert result.is_success is False
        assert result.final_task_status is TaskStatus.FAILED
        assert result.plan_review_handoff is not None
        assert result.plan_review_handoff.approval_id is None
        cast("AsyncMock", gate.request_plan_approval).assert_awaited_once()
        cast("AsyncMock", gate.fail_plan).assert_awaited_once()

    async def test_splittable_dispatches_team_without_gate(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
            post_task=_post_task(TaskStatus.COMPLETED),
        )
        # No plan-review gate attached: splittable work dispatches straight
        # to the team (the gate is opt-in, wired only when enabled).
        result = await pipeline.run(_work_item())

        assert result.execution_path is ExecutionPath.TEAM
        coordinator.coordinate.assert_awaited_once()
        coordinator.plan_preview.assert_not_called()


class TestPlanRequired:
    """An objective/charter (``plan_required``) is never a solo leaf.

    The spine forces the plan path regardless of the solo-vs-team router, so
    a product brief the router would classify LEAF still decomposes into a
    plan: parked for approval when the gate is wired, dispatched to the team
    otherwise. It never collapses to a single solo agent.
    """

    async def test_plan_required_parks_plan_over_leaf_verdict(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(),
            # The router says LEAF; plan_required overrides it to SPLITTABLE.
            verdict=RoutingVerdict.LEAF,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=3,
                    detail="3 subtasks awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        result = await pipeline.run(_work_item(plan_required=True))

        assert result.execution_path is ExecutionPath.PLAN_REVIEW
        assert result.verdict is RoutingVerdict.SPLITTABLE
        coordinator.plan_preview.assert_awaited_once()
        cast("AsyncMock", handles["worker"]).execute_once.assert_not_called()

    async def test_plan_required_dispatches_team_over_leaf_without_gate(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(),
            verdict=RoutingVerdict.LEAF,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
            post_task=_post_task(TaskStatus.COMPLETED),
        )

        result = await pipeline.run(_work_item(plan_required=True))

        assert result.execution_path is ExecutionPath.TEAM
        coordinator.coordinate.assert_awaited_once()
        cast("AsyncMock", handles["worker"]).execute_once.assert_not_called()


class TestOwnerStaffing:
    """A planned initiative is staffed with an accountable owner.

    The owner is stamped as the project's durable ``lead`` and threaded into
    the decomposition context so the planning stage runs AS the owner.
    """

    async def test_plan_required_staffs_and_threads_owner(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(lead=None),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=2,
                    detail="2 subtasks awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        await pipeline.run(_work_item(plan_required=True))

        # The staffed owner was persisted as the project's durable lead.
        project_repo = cast("AsyncMock", handles["project_repo"])
        project_repo.update.assert_awaited_once()
        stamped = project_repo.update.await_args.args[0]
        owner = cast("AgentIdentity", handles["identity"])
        assert stamped.lead == str(owner.id)
        # And the owner rides the decomposition context into planning.
        ctx = coordinator.plan_preview.await_args.args[0]
        assert ctx.decomposition_context.owner_identity is not None
        assert ctx.decomposition_context.owner_identity.id == owner.id

    async def test_already_led_project_keeps_its_lead(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        owner = make_e2e_identity()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(lead=str(owner.id)),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(owner,),
        )
        # The durable lead resolves via the registry regardless of status, so
        # a paused or offboarded lead is still threaded in rather than dropped.
        cast("AsyncMock", handles["registry"]).get.return_value = owner
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=1,
                    detail="1 subtask awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        await pipeline.run(_work_item(plan_required=True))

        # An already-led project is not re-stamped, and the existing lead is
        # resolved via the registry and threaded into planning.
        cast("AsyncMock", handles["project_repo"]).update.assert_not_awaited()
        cast("AsyncMock", handles["registry"]).get.assert_awaited_once_with(
            str(owner.id)
        )

    async def test_orphaned_lead_proceeds_unowned(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(lead="ghost-agent"),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        # The durable lead no longer resolves to a known agent (offboarded).
        cast("AsyncMock", handles["registry"]).get.return_value = None
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=1,
                    detail="1 subtask awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        await pipeline.run(_work_item(plan_required=True))

        # An orphaned lead is not re-stamped; planning proceeds unowned rather
        # than threading a phantom identity.
        cast("AsyncMock", handles["project_repo"]).update.assert_not_awaited()
        ctx = coordinator.plan_preview.await_args.args[0]
        assert ctx.decomposition_context.owner_identity is None

    async def test_empty_roster_stamps_no_lead(self) -> None:
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(lead=None),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(),
        )
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=1,
                    detail="1 subtask awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        # With no agent to staff, owner resolution yields no lead (the
        # roster-empty path); the run then fails gracefully because a team plan
        # needs at least one agent. Rather than raising a 500, the plan shell is
        # opened then marked FAILED and the run reports failure. The critical
        # invariant is that no phantom lead is stamped on the way to that failure.
        result = await pipeline.run(_work_item(plan_required=True))
        assert result.is_success is False
        assert result.final_task_status is TaskStatus.FAILED
        assert result.execution_path is ExecutionPath.PLAN_REVIEW
        assert result.plan_review_handoff is not None
        assert result.plan_review_handoff.approval_id is None
        cast("AsyncMock", gate.open_plan).assert_awaited_once()
        cast("AsyncMock", gate.fail_plan).assert_awaited_once()
        cast("AsyncMock", gate.request_plan_approval).assert_not_awaited()
        cast("AsyncMock", handles["project_repo"]).update.assert_not_awaited()

    async def test_concurrent_runs_stamp_a_single_lead(self) -> None:
        """The per-project lock serialises the owner read-modify-write.

        Two concurrent runs for the same unled project must not both observe
        ``lead is None`` and race to stamp a different lead: the first stamps,
        the second re-reads under the lock and resolves the stamped lead.
        """
        coordinator = mock_of[MultiAgentCoordinator]()
        coordinator.plan_preview.return_value = _preview()
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=_project(lead=None),
            verdict=RoutingVerdict.SPLITTABLE,
            coordinator=coordinator,
            agents=(make_e2e_identity(),),
        )
        owner = cast("AgentIdentity", handles["identity"])
        # A stateful project store: ``update`` persists the stamped lead so the
        # second run's re-read under the lock observes it.
        stored = {"project": _project(lead=None)}

        async def _get(_project_id: str) -> Project:
            return stored["project"]

        async def _update(
            project: Project, *, expected_version: int | None = None
        ) -> Project:
            current = stored["project"]
            if expected_version is not None and current.version != expected_version:
                msg = "project modified concurrently"
                raise PersistenceVersionConflictError(msg)
            stored["project"] = project
            return project

        project_repo = cast("AsyncMock", handles["project_repo"])
        project_repo.get.side_effect = _get
        project_repo.update.side_effect = _update
        cast("AsyncMock", handles["registry"]).get.return_value = owner
        gate = mock_of[PlanReviewGate](
            request_plan_approval=AsyncMock(
                return_value=PlanReviewHandoff(
                    approval_id="appr-1",
                    plan_id="plan-1",
                    subtask_count=1,
                    detail="1 subtask awaiting approval",
                )
            )
        )
        pipeline.attach_plan_review_gate(gate)

        await asyncio.gather(
            pipeline.run(_work_item(plan_required=True)),
            pipeline.run(_work_item(plan_required=True)),
        )

        # Exactly one stamp survives, and both runs agree on the same lead.
        project_repo.update.assert_awaited_once()
        assert stored["project"].lead == str(owner.id)
        ctx = coordinator.plan_preview.await_args.args[0]
        assert ctx.decomposition_context.owner_identity is not None
        assert ctx.decomposition_context.owner_identity.id == owner.id


class TestIntakeSplit:
    """The intake_only / continue_from_intake split used by the async
    conversational-execution path (surface the task id, then background the
    remaining spine)."""

    async def test_intake_only_creates_task_without_executing(self) -> None:
        pipeline, handles = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
        )
        task = await pipeline.intake_only(_work_item())
        assert str(task.id) == sid("task-1")
        # Intake ran; the post-intake spine did not.
        cast("AsyncMock", handles["worker"]).execute_once.assert_not_called()

    async def test_continue_from_intake_runs_spine_without_reintake(self) -> None:
        intake_engine = mock_of[IntakeEngine]()
        task_engine = mock_of[TaskEngine]()
        task_engine.get_task.return_value = _post_task(TaskStatus.IN_REVIEW)
        project_repo = mock_of[ProjectRepository]()
        project_repo.get.return_value = mock_of[Project]()
        routing_policy = mock_of[WorkRoutingPolicy]()
        routing_policy.decide.return_value = RoutingVerdict.LEAF
        scorer = mock_of[AgentTaskScorer]()
        scorer.min_score = _MIN_SCORE
        scorer.low_confidence_score = _LOW_CONFIDENCE_SCORE
        scorer.score.return_value = RoutingCandidate(
            agent_identity=make_e2e_identity(), score=0.9, reason="test"
        )
        worker = mock_of[WorkerExecutionService]()
        worker.execute_once.return_value = _post_task(TaskStatus.IN_REVIEW)
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (make_e2e_identity(),)
        pipeline = DefaultWorkPipeline(
            intake_engine=intake_engine,
            task_engine=task_engine,
            project_repository=project_repo,
            routing_policy=routing_policy,
            scorer=scorer,
            worker_execution_service=worker,
            coordinator=None,
            agent_registry=registry,
            clock=FakeClock(),
        )

        result = await pipeline.continue_from_intake(_work_item(), _task())

        assert result.execution_path is ExecutionPath.SOLO
        assert result.final_task_status is TaskStatus.IN_REVIEW
        worker.execute_once.assert_awaited_once()
        # continue_from_intake must not re-run intake on the already-created task.
        cast("AsyncMock", intake_engine.process).assert_not_called()


class TestNarratorTrigger:
    def _solo_pipeline(self) -> DefaultWorkPipeline:
        pipeline, _ = _pipeline(
            intake_result=IntakeResult.accepted_result(
                request_id="corr-1", task_id="task-1"
            ),
            task=_task(),
            project=mock_of[Project](),
            verdict=RoutingVerdict.LEAF,
            coordinator=None,
            agents=(make_e2e_identity(),),
            post_task=_post_task(TaskStatus.IN_REVIEW),
        )
        return pipeline

    async def test_narrator_invoked_on_completion(self) -> None:
        narrator = mock_of[RunNarrator](generate=AsyncMock(return_value=None))
        pipeline = self._solo_pipeline()
        pipeline.attach_narrator(narrator)
        await pipeline.run(_work_item())
        narrator.generate.assert_awaited_once_with(
            task_id=sid("task-1"), project_id="proj-1"
        )

    async def test_narrator_failure_does_not_fail_run(self) -> None:
        narrator = mock_of[RunNarrator](
            generate=AsyncMock(side_effect=RuntimeError("narration broke"))
        )
        pipeline = self._solo_pipeline()
        pipeline.attach_narrator(narrator)
        result = await pipeline.run(_work_item())
        assert result.is_success is True
        assert result.final_task_status is TaskStatus.IN_REVIEW

    async def test_no_narrator_is_noop(self) -> None:
        pipeline = self._solo_pipeline()
        result = await pipeline.run(_work_item())
        assert result.is_success is True
