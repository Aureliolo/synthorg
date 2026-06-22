"""B3: the solo path routes single-agent selection through the service.

Proves ``DefaultWorkPipeline._select_solo_agent`` delegates to
``TaskAssignmentService`` when one is wired, so the service's task-status
validation runs in production (an invalid status is rejected) and a
no-eligible result surfaces as a routing-undecidable error.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.assignment.models import AssignmentRequest, AssignmentResult
from synthorg.engine.assignment.registry import build_strategy_map
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.errors import TaskAssignmentError
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.pipeline.errors import WorkRoutingUndecidableError
from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.workers.execution_service import WorkerExecutionService
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

_PERMISSIVE_MIN_SCORE = 0.0


def _agent(name: str) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(),
    )


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
        project="proj-1",
        created_by="operator-1",
        status=status,
        assigned_to=assigned_to,
    )


class _NoSelectionStrategy:
    """Strategy that always returns an empty selection."""

    name = "stub-empty"

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self.name,
            reason="no eligible agent in the test stub",
        )


def _pipeline(service: TaskAssignmentService) -> DefaultWorkPipeline:
    scorer = AgentTaskScorer(min_score=_PERMISSIVE_MIN_SCORE)
    return DefaultWorkPipeline(
        intake_engine=mock_of[IntakeEngine](),
        task_engine=mock_of[TaskEngine](),
        project_repository=mock_of[ProjectRepository](),
        routing_policy=mock_of[WorkRoutingPolicy](),
        scorer=scorer,
        worker_execution_service=mock_of[WorkerExecutionService](),
        coordinator=None,
        agent_registry=mock_of[AgentRegistryService](),
        clock=FakeClock(),
        assignment_service=service,
    )


def _role_based_service() -> TaskAssignmentService:
    scorer = AgentTaskScorer(min_score=_PERMISSIVE_MIN_SCORE)
    strategy = build_strategy_map(scorer=scorer)["role_based"]
    return TaskAssignmentService(strategy)


def test_solo_pick_routes_through_service() -> None:
    pipeline = _pipeline(_role_based_service())
    agent = _agent("dev-1")

    selected = pipeline._select_solo_agent(_task(), (agent,))

    assert selected == str(agent.id)


def test_non_assignable_status_is_rejected_by_service() -> None:
    pipeline = _pipeline(_role_based_service())
    agent = _agent("dev-1")

    # IN_PROGRESS is not in the service's assignable-status set, so the
    # service rejects the request before any scoring happens.
    in_progress = _task(TaskStatus.IN_PROGRESS, assigned_to="agent-prior")
    with pytest.raises(TaskAssignmentError):
        pipeline._select_solo_agent(in_progress, (agent,))


def test_no_selection_surfaces_routing_undecidable() -> None:
    pipeline = _pipeline(TaskAssignmentService(_NoSelectionStrategy()))
    agent = _agent("dev-1")

    with pytest.raises(WorkRoutingUndecidableError):
        pipeline._select_solo_agent(_task(), (agent,))
