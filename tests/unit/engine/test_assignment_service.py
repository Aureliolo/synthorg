"""Unit tests for TaskAssignmentService."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import (
    Complexity,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.assignment.models import (
    AssignmentRequest,
)
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.assignment.strategies import (
    ManualAssignmentStrategy,
    RoleBasedAssignmentStrategy,
)
from synthorg.engine.errors import TaskAssignmentError
from synthorg.engine.routing.scorer import AgentTaskScorer

pytestmark = pytest.mark.unit


def _model_config() -> ModelConfig:
    return ModelConfig(provider="test-provider", model_id="test-small-001")


def _make_agent(
    name: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    primary_skills: tuple[str, ...] = (),
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=primary_skills),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-001",
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "manager",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


class TestTaskAssignmentService:
    """TaskAssignmentService tests."""

    def test_delegates_to_strategy(self) -> None:
        """Service delegates to the configured strategy."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        agent = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        task = _make_task(estimated_complexity=Complexity.MEDIUM)

        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python",),
        )

        result = service.assign(request)

        assert result.strategy_used == "role_based"
        assert result.selected is not None

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.CREATED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
        ],
        ids=["created", "failed", "interrupted"],
    )
    def test_accepts_assignable_statuses(
        self,
        status: TaskStatus,
    ) -> None:
        """Service accepts CREATED, FAILED, and INTERRUPTED tasks."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        agent = _make_agent("dev-1", primary_skills=("python",))
        overrides: dict[str, object] = {"status": status.value}
        if status != TaskStatus.CREATED:
            overrides["assigned_to"] = str(agent.id)
        task = _make_task(**overrides)

        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python",),
        )

        result = service.assign(request)
        assert result is not None

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        ],
    )
    def test_rejects_non_assignable_statuses(
        self,
        status: TaskStatus,
    ) -> None:
        """Service rejects tasks with non-assignable statuses."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        agent = _make_agent("dev-1")

        # Build task with appropriate fields for the status
        overrides: dict[str, object] = {"status": status.value}

        # Statuses that require assigned_to
        requires_assignee = {
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
        }
        if status in requires_assignee:
            overrides["assigned_to"] = str(agent.id)

        task = _make_task(**overrides)

        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        with pytest.raises(TaskAssignmentError, match="status"):
            service.assign(request)

    def test_error_propagation(self) -> None:
        """Strategy errors propagate through the service."""
        strategy = ManualAssignmentStrategy()
        service = TaskAssignmentService(strategy)

        # Task without assigned_to will cause ManualAssignmentStrategy to fail
        task = _make_task()
        agent = _make_agent("dev-1")

        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        with pytest.raises(TaskAssignmentError, match="assigned_to"):
            service.assign(request)

    def test_unexpected_exception_propagates(self) -> None:
        """Unexpected exceptions propagate through the service."""

        class _BrokenStrategy:
            @property
            def name(self) -> str:
                return "broken"

            def assign(
                self,
                request: AssignmentRequest,
            ) -> None:
                msg = "unexpected boom"
                raise RuntimeError(msg)

        service = TaskAssignmentService(_BrokenStrategy())  # type: ignore[arg-type]

        agent = _make_agent("dev-1")
        task = _make_task()
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        with pytest.raises(RuntimeError, match="unexpected boom"):
            service.assign(request)

    def test_result_contains_strategy_name(self) -> None:
        """Result contains the name of the strategy used."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        agent = _make_agent("dev-1", primary_skills=("python",))
        task = _make_task()

        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python",),
        )

        result = service.assign(request)

        assert result.strategy_used == "role_based"
