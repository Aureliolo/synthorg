"""Unit tests for the default work pipeline service."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineTeamPathUnavailableError,
    WorkProjectNotFoundError,
    WorkRoutingUndecidableError,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
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


def _pipeline(  # noqa: PLR0913 -- test builder with keyword-only knobs
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
        with pytest.raises(WorkProjectNotFoundError):
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
