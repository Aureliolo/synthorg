"""Tests for task routing service."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Skill
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStructure, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.routing.topology_selector import TopologySelector
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.engine.routing_policy.config import CapabilityPolicyConfig
from synthorg.hr.enums import AgentStatus
from tests._shared import as_uuid, sid


def _make_agent(
    name: str,
    *,
    primary: tuple[str, ...] = (),
    secondary: tuple[str, ...] = (),
    role: str = "developer",
    status: AgentStatus = AgentStatus.ACTIVE,
    capability: CapabilityLevel | None = None,
) -> AgentIdentity:
    """Helper to create a named agent."""
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role=role,
        department="Engineering",
        skills=SkillSet(
            primary=tuple(Skill(id=s, name=s) for s in primary),
            secondary=tuple(Skill(id=s, name=s) for s in secondary),
        ),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-model-001",
            capability=capability,
        ),
        hiring_date=date(2026, 1, 1),
        status=status,
    )


def _capability_policy() -> CapabilityPolicy:
    """The policy with its shipped floors, reading each agent's own rung.

    No provider registry here, so the roster's rung is the only source, which
    is what the live path falls back to for a pair the registry has not graded.
    """

    class _RosterOnly:
        def capability_for_pair(
            self,
            provider: str,
            model_id: str,
            *,
            claimed: CapabilityLevel | None,
        ) -> CapabilityLevel | None:
            return claimed

    return CapabilityPolicy(config=CapabilityPolicyConfig(), reader=_RosterOnly())


def _make_task(task_id: str = "task-route-1") -> Task:
    """Helper to create a minimal task."""
    return Task(
        id=as_uuid(task_id),
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
        id=as_uuid(task_id),
        title=f"Subtask {task_id}",
        description=f"Description for {task_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
        parent_task_id=parent_task_id,
    )


def _make_decomposition_result(
    parent_task_id: str = sid("task-route-1"),
) -> DecompositionResult:
    """Helper to create a decomposition result."""
    plan = DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="Backend Work",
                description="Backend development",
                required_skills=("python", "sql"),
                required_role="developer",
                estimated_complexity=Complexity.MEDIUM,
                expected_artifacts=("src/backend.py",),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="Frontend Work",
                description="Frontend development",
                required_skills=("javascript", "react"),
                required_role="frontend-developer",
                estimated_complexity=Complexity.MEDIUM,
                dependencies=(sid("sub-1"),),
                expected_artifacts=("src/frontend.tsx",),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            _make_child_task("sub-1", parent_task_id),
            _make_child_task("sub-2", parent_task_id),
        ),
        dependency_edges=((sid("sub-1"), sid("sub-2")),),
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
        )
        frontend_dev = _make_agent(
            "Frontend Dev",
            primary=("javascript", "react"),
            role="frontend-developer",
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
        sub1_decision = next(
            d for d in result.decisions if d.subtask_id == sid("sub-1")
        )
        assert sub1_decision.selected_candidate.agent_identity.name == "Backend Dev"

        # sub-2 should go to frontend dev
        sub2_decision = next(
            d for d in result.decisions if d.subtask_id == sid("sub-2")
        )
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
        )

        task = _make_task()
        decomp = _make_decomposition_result()

        result = service.route(decomp, (agent,), task)

        assert len(result.unroutable) == 2
        assert sid("sub-1") in result.unroutable
        assert sid("sub-2") in result.unroutable

    @pytest.mark.unit
    def test_an_overqualified_role_holder_is_reached_past_the_exact_rung(self) -> None:
        """The capability ladder is a preference, never a filter.

        Banding to the exact rung and scoring only inside it made a specialist
        one rung ABOVE the requirement unreachable while any exact-rung
        stranger existed. On a roster whose agents declare no skills -- which
        every shipped template produces -- the role bonus is the only score
        that can fire, so this stranded five of six plan items against a roster
        that staffed every role they named.
        """
        scorer = AgentTaskScorer()
        service = TaskRoutingService(
            scorer, TopologySelector(), capability=_capability_policy()
        )
        exact_rung_stranger = _make_agent(
            "Backend Dev",
            role="developer",
            capability="capable",
        )
        overqualified_specialist = _make_agent(
            "Frontend Dev",
            role="frontend-developer",
            capability="expert",
        )

        result = service.route(
            _make_decomposition_result(),
            (exact_rung_stranger, overqualified_specialist),
            _make_task(),
        )

        assert result.unroutable == ()
        chosen = {
            d.subtask_id: d.selected_candidate.agent_identity.name
            for d in result.decisions
        }
        assert chosen[sid("sub-2")] == "Frontend Dev"

    @pytest.mark.unit
    def test_a_subtask_no_rung_can_serve_is_still_unroutable(self) -> None:
        """Walking the whole ladder must not become "route it to anyone"."""
        service = TaskRoutingService(
            AgentTaskScorer(), TopologySelector(), capability=_capability_policy()
        )
        unrelated = _make_agent("Chef", role="chef", capability="expert")

        result = service.route(_make_decomposition_result(), (unrelated,), _make_task())

        assert len(result.unroutable) == 2

    @pytest.mark.unit
    def test_an_unresolvable_binding_is_refused_and_reported_as_one(self) -> None:
        """An ungraded pair is a broken binding, not a weak agent.

        The policy refuses it at every stakes level, so the agent is
        assignable nothing in the whole org while its roster row reads
        available. Folding that into the same boolean as "too weak for these
        stakes" left the condition with no name anywhere an operator looks.
        """
        service = TaskRoutingService(
            AgentTaskScorer(), TopologySelector(), capability=_capability_policy()
        )
        # Its role is exactly what sub-2 asks for; only the rung is missing.
        ungraded = _make_agent(
            "Frontend Dev", role="frontend-developer", capability=None
        )
        subtask = _make_decomposition_result().plan.subtasks[1]

        admissible = service._sanctioned(subtask, (ungraded,))

        assert admissible.admitted == ()
        assert admissible.unresolved == (ungraded,)
        assert (
            service.route(
                _make_decomposition_result(), (ungraded,), _make_task()
            ).unroutable
        ) == (sid("sub-1"), sid("sub-2"))

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
        )
        agent2 = _make_agent(
            "Mid Dev",
            primary=("python",),
            secondary=("sql",),
            role="developer",
        )

        plan = DecompositionPlan(
            parent_task_id=sid("task-route-1"),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="Python Work",
                    description="Python development",
                    required_skills=("python",),
                    required_role="developer",
                    estimated_complexity=Complexity.MEDIUM,
                    expected_artifacts=("src/python_work.py",),
                ),
            ),
            task_structure=TaskStructure.SEQUENTIAL,
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
        assert result.parent_task_id == sid("task-route-1")

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
