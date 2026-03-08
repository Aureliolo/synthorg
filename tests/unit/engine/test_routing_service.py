"""Tests for task routing service."""

from datetime import date
from uuid import uuid4

import pytest

from ai_company.core.agent import AgentIdentity, ModelConfig, SkillSet
from ai_company.core.enums import (
    AgentStatus,
    Complexity,
    Priority,
    SeniorityLevel,
    TaskType,
)
from ai_company.core.task import Task
from ai_company.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from ai_company.engine.routing.scorer import AgentTaskScorer
from ai_company.engine.routing.service import TaskRoutingService
from ai_company.engine.routing.topology_selector import TopologySelector


def _make_agent(  # noqa: PLR0913
    name: str,
    *,
    primary: tuple[str, ...] = (),
    secondary: tuple[str, ...] = (),
    role: str = "developer",
    level: SeniorityLevel = SeniorityLevel.MID,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    """Helper to create a named agent."""
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role=role,
        department="Engineering",
        level=level,
        skills=SkillSet(primary=primary, secondary=secondary),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=status,
    )


def _make_task(task_id: str = "task-route-1") -> Task:
    """Helper to create a minimal task."""
    return Task(
        id=task_id,
        title="Routing Test",
        description="Testing routing",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
    )


def _make_child_task(task_id: str, parent_task_id: str = "task-route-1") -> Task:
    """Helper to create a child task for decomposition results."""
    return Task(
        id=task_id,
        title=f"Subtask {task_id}",
        description=f"Description for {task_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
        parent_task_id=parent_task_id,
    )


def _make_decomposition_result(
    parent_task_id: str = "task-route-1",
) -> DecompositionResult:
    """Helper to create a decomposition result."""
    plan = DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=(
            SubtaskDefinition(
                id="sub-1",
                title="Backend Work",
                description="Backend development",
                required_skills=("python", "sql"),
                required_role="developer",
                estimated_complexity=Complexity.MEDIUM,
            ),
            SubtaskDefinition(
                id="sub-2",
                title="Frontend Work",
                description="Frontend development",
                required_skills=("javascript", "react"),
                required_role="frontend-developer",
                estimated_complexity=Complexity.MEDIUM,
                dependencies=("sub-1",),
            ),
        ),
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            _make_child_task("sub-1", parent_task_id),
            _make_child_task("sub-2", parent_task_id),
        ),
        dependency_edges=(("sub-1", "sub-2"),),
    )


class TestTaskRoutingService:
    """Tests for TaskRoutingService."""

    @pytest.mark.unit
    def test_routes_to_best_agent(self) -> None:
        """Routes subtask to the highest-scoring agent."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        backend_dev = _make_agent(
            "Backend Dev",
            primary=("python", "sql"),
            role="developer",
            level=SeniorityLevel.MID,
        )
        frontend_dev = _make_agent(
            "Frontend Dev",
            primary=("javascript", "react"),
            role="frontend-developer",
            level=SeniorityLevel.MID,
        )

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(
            decomp,
            (backend_dev, frontend_dev),
            task,
        )

        assert len(result.decisions) == 2
        assert len(result.unroutable) == 0

        # sub-1 should go to backend dev
        sub1_decision = next(d for d in result.decisions if d.subtask_id == "sub-1")
        assert sub1_decision.selected_candidate.agent_identity.name == "Backend Dev"

        # sub-2 should go to frontend dev
        sub2_decision = next(d for d in result.decisions if d.subtask_id == "sub-2")
        assert sub2_decision.selected_candidate.agent_identity.name == "Frontend Dev"

    @pytest.mark.unit
    def test_unroutable_subtasks(self) -> None:
        """Subtasks with no viable agent are reported as unroutable."""
        scorer = AgentTaskScorer(min_score=0.5)
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        # Agent with no matching skills
        agent = _make_agent(
            "Unrelated Agent",
            primary=("cooking",),
            role="chef",
            level=SeniorityLevel.JUNIOR,
        )

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (agent,), task)

        assert len(result.unroutable) == 2
        assert "sub-1" in result.unroutable
        assert "sub-2" in result.unroutable

    @pytest.mark.unit
    def test_alternatives_populated(self) -> None:
        """Alternatives include other viable candidates."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        agent1 = _make_agent(
            "Senior Dev",
            primary=("python", "sql"),
            role="developer",
            level=SeniorityLevel.SENIOR,
        )
        agent2 = _make_agent(
            "Mid Dev",
            primary=("python",),
            secondary=("sql",),
            role="developer",
            level=SeniorityLevel.MID,
        )

        plan = DecompositionPlan(
            parent_task_id="task-route-1",
            subtasks=(
                SubtaskDefinition(
                    id="sub-1",
                    title="Python Work",
                    description="Python development",
                    required_skills=("python",),
                    required_role="developer",
                    estimated_complexity=Complexity.MEDIUM,
                ),
            ),
        )
        decomp = DecompositionResult(
            plan=plan,
            created_tasks=(_make_child_task("sub-1"),),
        )
        task = _make_task()

        result = service.route(decomp, (agent1, agent2), task)

        assert len(result.decisions) == 1
        decision = result.decisions[0]
        assert len(decision.alternatives) == 1

    @pytest.mark.unit
    def test_topology_applied(self) -> None:
        """Topology is applied to all routing decisions."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        agent = _make_agent(
            "Dev",
            primary=("python", "sql"),
            role="developer",
            level=SeniorityLevel.MID,
        )

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (agent,), task)

        for decision in result.decisions:
            assert decision.topology is not None

    @pytest.mark.unit
    def test_empty_agents(self) -> None:
        """No available agents -> all subtasks unroutable."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (), task)

        assert len(result.decisions) == 0
        assert len(result.unroutable) == 2

    @pytest.mark.unit
    def test_inactive_agents_filtered(self) -> None:
        """Inactive agents score 0 and don't get routed."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        agent = _make_agent(
            "Terminated Dev",
            primary=("python", "sql"),
            role="developer",
            status=AgentStatus.TERMINATED,
        )

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (agent,), task)
        assert len(result.unroutable) == 2

    @pytest.mark.unit
    def test_parent_task_id_in_result(self) -> None:
        """Result carries the correct parent_task_id."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (), task)
        assert result.parent_task_id == "task-route-1"

    @pytest.mark.unit
    def test_parent_task_id_mismatch_raises(self) -> None:
        """ValueError when parent_task.id != plan.parent_task_id."""
        scorer = AgentTaskScorer()
        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        task = _make_task("task-wrong-id")
        decomp = _make_decomposition_result("task-route-1")

        with pytest.raises(ValueError, match="does not match"):
            service.route(decomp, (), task)

    @pytest.mark.unit
    def test_exception_propagates(self) -> None:
        """Exceptions from _do_route are logged and re-raised."""
        from unittest.mock import MagicMock

        scorer = MagicMock(spec=AgentTaskScorer)
        scorer.min_score = 0.1
        scorer.score.side_effect = RuntimeError("scorer boom")

        selector = TopologySelector()
        service = TaskRoutingService(scorer, selector)

        task = _make_task()
        decomp = _make_decomposition_result()

        agent = _make_agent(
            "Dev",
            primary=("python",),
        )

        with pytest.raises(RuntimeError, match="scorer boom"):
            service.route(decomp, (agent,), task)
