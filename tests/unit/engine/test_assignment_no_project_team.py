"""An assignment request carries no per-project roster subset.

Which agents may take a project's work was a stored subset nothing in the
loop ever wrote, so the filter it fed refused nobody and hid the real
question (which agent fits the work). Confinement is structural instead:
the per-project workspace root, the sandbox container key, forge
repo-scoping and the SecOps action-type gate.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Skill
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filters import IdentityPoolFilter
from synthorg.engine.assignment.rankers import ScoreDescendingRanker
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.routing.scorer import AgentTaskScorer
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _model_config() -> ModelConfig:
    return ModelConfig(provider="test-provider", model_id="test-basic-001")


def _make_agent(
    name: str,
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=(Skill(id="python", name="python"),)),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": as_uuid("task-001"),
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "manager",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _make_service() -> TaskAssignmentService:
    scorer = AgentTaskScorer()
    strategy = ScoringBasedAssignmentStrategy(
        name="role_based",
        scorer=scorer,
        pool_filter=IdentityPoolFilter(),
        ranker=ScoreDescendingRanker(),
    )
    return TaskAssignmentService(strategy)


class TestNoProjectTeamFilter:
    def test_the_request_refuses_a_project_team(self) -> None:
        alice = _make_agent("Alice")

        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AssignmentRequest(
                task=_make_task(),
                available_agents=(alice,),
                project_team=(str(alice.id),),  # type: ignore[call-arg]
            )

    def test_every_available_agent_is_considered(self) -> None:
        """Selection ranks the whole pool; no roster subset narrows it first."""
        alice = _make_agent("Alice")
        bob = _make_agent("Bob")
        service = _make_service()

        result = service.assign(
            AssignmentRequest(
                task=_make_task(),
                available_agents=(alice, bob),
                required_role="Developer",
            )
        )

        assert result.selected is not None
        assert result.selected.agent_identity.id in {alice.id, bob.id}
