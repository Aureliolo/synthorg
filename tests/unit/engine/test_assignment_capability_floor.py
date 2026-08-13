"""Assignment refuses an agent whose model cannot carry the work.

The floor is a hard filter above the scoring rather than a score input: an
agent whose model cannot carry the work does not become able to by fitting
the role well, and the previous design's answer -- quietly running the turn
on a stronger model -- is what made one agent's history a mix of whatever
the stakes ladder reached for.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Skill
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filters import IdentityPoolFilter
from synthorg.engine.assignment.rankers import ScoreDescendingRanker
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing_policy import (
    CapabilityFloorPolicy,
    ResolvedAgentCapabilityReader,
    StakesCapabilityFloor,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_PROVIDER = "test-provider"
_MODEL_IDS: dict[CapabilityLevel, str] = {
    "basic": "test-basic-001",
    "capable": "test-capable-001",
    "expert": "test-expert-001",
}


def _resolver() -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {}
    for rung, model_id in _MODEL_IDS.items():
        resolved = ResolvedModel(
            provider_name=_PROVIDER,
            model_id=model_id,
            cost_per_1k_input=0.1,
            cost_per_1k_output=0.1,
            capability=rung,
        )
        index[model_id] = (resolved,)
    return ModelResolver(index)


def _policy() -> CapabilityFloorPolicy:
    return CapabilityFloorPolicy(
        floors=StakesCapabilityFloor(),
        reader=ResolvedAgentCapabilityReader(_resolver()),
    )


def _agent(name: str, rung: CapabilityLevel) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider=_PROVIDER,
            model_id=_MODEL_IDS[rung],
            capability=rung,
        ),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=(Skill(id="python", name="python"),)),
    )


def _task(stakes: Stakes) -> Task:
    return Task(
        id=as_uuid("task-001"),
        title="Test task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        stakes=stakes,
    )


def _strategy(
    *,
    capability_floor: CapabilityFloorPolicy | None,
) -> ScoringBasedAssignmentStrategy:
    return ScoringBasedAssignmentStrategy(
        name="role_based",
        scorer=AgentTaskScorer(),
        pool_filter=IdentityPoolFilter(),
        ranker=ScoreDescendingRanker(),
        capability_floor=capability_floor,
    )


def _service(
    *,
    capability_floor: CapabilityFloorPolicy | None,
) -> TaskAssignmentService:
    return TaskAssignmentService(
        _strategy(capability_floor=capability_floor),
        capability_floor=capability_floor,
    )


def _request(stakes: Stakes, *agents: AgentIdentity) -> AssignmentRequest:
    return AssignmentRequest(
        task=_task(stakes),
        available_agents=agents,
        required_role="Developer",
        stakes=stakes,
    )


class TestTheFloorFiltersTheRoster:
    def test_high_stakes_work_skips_the_agent_that_cannot_carry_it(self) -> None:
        service = _service(capability_floor=_policy())

        result = service.assign(
            _request(Stakes.HIGH, _agent("Weak", "basic"), _agent("Strong", "expert")),
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Strong"

    def test_low_stakes_work_leaves_the_whole_roster_eligible(self) -> None:
        service = _service(capability_floor=_policy())

        result = service.assign(
            _request(Stakes.LOW, _agent("Weak", "basic"), _agent("Strong", "expert")),
        )

        assert result.selected is not None
        assert {a.agent_identity.name for a in result.alternatives} | {
            result.selected.agent_identity.name
        } == {"Weak", "Strong"}

    def test_no_agent_at_the_rung_parks_and_names_it(self) -> None:
        """The fix is an agent at the needed rung, so the reason says so."""
        service = _service(capability_floor=_policy())

        result = service.assign(_request(Stakes.CRITICAL, _agent("Weak", "basic")))

        assert result.selected is None
        assert "expert" in result.reason
        assert "critical" in result.reason

    def test_an_under_capable_agent_is_not_even_an_alternative(self) -> None:
        """A hard filter, not a preference: no score compensates for it.

        Both agents fit the role identically, so nothing in the ranking
        separates them. The weak one still does not appear, because it could
        not do the work if the strong one were unavailable.
        """
        service = _service(capability_floor=_policy())

        result = service.assign(
            _request(
                Stakes.CRITICAL,
                _agent("Weak", "basic"),
                _agent("Strong", "expert"),
            ),
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Strong"
        assert result.alternatives == ()


class TestTheServiceOwnsTheFloor:
    def test_the_service_stamps_the_floor_a_caller_omitted(self) -> None:
        """A caller cannot assign consequential work under a weaker floor."""
        service = _service(capability_floor=_policy())

        result = service.assign(_request(Stakes.HIGH, _agent("Weak", "basic")))

        assert result.selected is None

    def test_an_unwired_floor_gates_nothing(self) -> None:
        """An installation with no capability registry still assigns work."""
        service = _service(capability_floor=None)

        result = service.assign(_request(Stakes.CRITICAL, _agent("Weak", "basic")))

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Weak"

    def test_the_registry_rung_decides_not_the_roster_one(self) -> None:
        """A roster row claiming expert does not staff expert work."""
        service = _service(capability_floor=_policy())
        liar = _agent("Liar", "basic").model_copy(
            update={
                "model": ModelConfig(
                    provider=_PROVIDER,
                    model_id=_MODEL_IDS["basic"],
                    capability="expert",
                ),
            },
        )

        result = service.assign(_request(Stakes.HIGH, liar))

        assert result.selected is None
