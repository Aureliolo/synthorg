"""B3: the solo path routes single-agent selection through the service.

Proves ``select_solo_agent`` delegates to ``TaskAssignmentService`` when
one is wired, so the service's task-status validation runs in production
(an invalid status is rejected) and a no-eligible result surfaces as a
routing-undecidable error.
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
from synthorg.engine.pipeline._solo_selection import select_solo_agent
from synthorg.engine.pipeline.errors import WorkRoutingUndecidableError
from synthorg.engine.routing.scorer import AgentTaskScorer
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_PERMISSIVE_MIN_SCORE = 0.0


def _agent(name: str) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
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


def _scorer() -> AgentTaskScorer:
    return AgentTaskScorer(min_score=_PERMISSIVE_MIN_SCORE)


def _role_based_service() -> TaskAssignmentService:
    strategy = build_strategy_map(scorer=_scorer())["role_based"]
    return TaskAssignmentService(strategy)


def test_solo_pick_routes_through_service() -> None:
    service = _role_based_service()
    agent = _agent("dev-1")

    selected = select_solo_agent(
        _task(), (agent,), scorer=_scorer(), assignment_service=service
    )

    assert selected == str(agent.id)


def test_non_assignable_status_is_rejected_by_service() -> None:
    service = _role_based_service()
    agent = _agent("dev-1")

    # IN_PROGRESS is not in the service's assignable-status set, so the
    # service rejects the request before any scoring happens.
    in_progress = _task(TaskStatus.IN_PROGRESS, assigned_to="agent-prior")
    with pytest.raises(TaskAssignmentError):
        select_solo_agent(
            in_progress, (agent,), scorer=_scorer(), assignment_service=service
        )


def test_no_selection_surfaces_routing_undecidable() -> None:
    service = TaskAssignmentService(_NoSelectionStrategy())
    agent = _agent("dev-1")

    with pytest.raises(WorkRoutingUndecidableError):
        select_solo_agent(
            _task(), (agent,), scorer=_scorer(), assignment_service=service
        )
