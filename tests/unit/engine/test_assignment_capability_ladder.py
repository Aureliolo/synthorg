"""Assignment walks the capability ladder over the roster.

The ladder runs above the scoring rather than as a score input: an agent
whose model cannot carry the work does not become able to by fitting the
role well, and the previous design's answer (quietly running the turn on a
stronger model) is what made one agent's history a mix of whatever the
stakes reached for.

Match, else the nearest rung above, else the nearest below with the
concession logged. Preferring the exact rung over a stronger one is the
org's standing cost discipline: it picks the cheapest agent that can do the
work, on every assignment rather than only past a budget threshold.
"""

from datetime import date
from typing import Final

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
    CapabilityPolicy,
    CapabilityPolicyConfig,
    ResolvedAgentCapabilityReader,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

#: Distinguishes "the caller said nothing" from "the caller said ``None``".
#: ``None`` is a meaningful value (no policy at all), so it cannot double as
#: the default that means "mirror the service's".
_UNSET: Final[CapabilityPolicy] = CapabilityPolicy(
    config=CapabilityPolicyConfig(),
    reader=ResolvedAgentCapabilityReader(ModelResolver({})),
)

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


def _policy(config: CapabilityPolicyConfig | None = None) -> CapabilityPolicy:
    return CapabilityPolicy(
        config=config if config is not None else CapabilityPolicyConfig(),
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
    capability: CapabilityPolicy | None,
) -> ScoringBasedAssignmentStrategy:
    return ScoringBasedAssignmentStrategy(
        name="role_based",
        scorer=AgentTaskScorer(),
        pool_filter=IdentityPoolFilter(),
        ranker=ScoreDescendingRanker(),
        capability=capability,
    )


def _service(
    *,
    capability: CapabilityPolicy | None,
    strategy_capability: CapabilityPolicy | None = _UNSET,
) -> TaskAssignmentService:
    """Build the service, with the strategy's policy settable independently.

    Defaulting the strategy's policy to the service's keeps the coupled
    behaviour every other test wants. Passing ``None`` explicitly is what
    lets one test build a strategy that holds NO policy, so the assertion is
    about what the SERVICE stamps rather than about what the strategy was
    already given.

    Returns:
        The wired service.
    """
    return TaskAssignmentService(
        _strategy(
            capability=(
                capability if strategy_capability is _UNSET else strategy_capability
            ),
        ),
        capability=capability,
    )


def _request(stakes: Stakes, *agents: AgentIdentity) -> AssignmentRequest:
    return AssignmentRequest(
        task=_task(stakes),
        available_agents=agents,
        required_role="Developer",
    )


class TestTheLadderNarrowsTheRoster:
    def test_high_stakes_work_skips_the_agent_that_cannot_carry_it(self) -> None:
        service = _service(capability=_policy())

        result = service.assign(
            _request(Stakes.HIGH, _agent("Weak", "basic"), _agent("Strong", "expert")),
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Strong"

    def test_an_exact_match_beats_a_stronger_agent(self) -> None:
        """The standing cost discipline: cheapest agent that can do the work.

        Normal-stakes work needs ``capable``. The expert agent could do it,
        and the ladder still prefers the exact rung, so the org does not pay
        expert prices for work a capable agent was staffed to do.
        """
        service = _service(capability=_policy())

        result = service.assign(
            _request(
                Stakes.NORMAL,
                _agent("Exact", "capable"),
                _agent("Stronger", "expert"),
            ),
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Exact"
        assert result.alternatives == ()

    def test_the_nearest_higher_rung_answers_when_no_exact_match_exists(self) -> None:
        service = _service(capability=_policy())

        result = service.assign(
            _request(Stakes.NORMAL, _agent("Weak", "basic"), _agent("Top", "expert")),
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Top"

    def test_low_stakes_take_the_nearest_lower_rung_as_a_last_resort(self) -> None:
        """Below the park floor a weaker agent still does the work."""
        service = _service(
            capability=_policy(
                CapabilityPolicyConfig.model_validate(
                    {
                        "capability_floors": {
                            "low": "expert",
                            "normal": "expert",
                            "high": "expert",
                            "critical": "expert",
                        }
                    }
                )
            )
        )

        result = service.assign(_request(Stakes.LOW, _agent("Weak", "basic")))

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Weak"

    def test_the_ranker_still_decides_within_the_band(self) -> None:
        """Narrowing to a band leaves the score / workload / cost axis alone."""
        service = _service(capability=_policy())
        fitting = _agent("Fitting", "capable")
        unskilled = _agent("Unskilled", "capable").model_copy(
            update={"skills": SkillSet(primary=(Skill(id="cobol", name="cobol"),))},
        )

        result = service.assign(
            AssignmentRequest(
                task=_task(Stakes.NORMAL),
                available_agents=(unskilled, fitting),
                required_role="Developer",
                required_skills=("python",),
            )
        )

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Fitting"

    def test_no_sanctioned_agent_parks_and_names_the_rung(self) -> None:
        """The fix is an agent at the needed rung, so the reason says so."""
        service = _service(capability=_policy())

        result = service.assign(_request(Stakes.CRITICAL, _agent("Weak", "basic")))

        assert result.selected is None
        assert "expert" in result.reason
        assert "critical" in result.reason

    def test_a_forbidden_weaker_agent_is_not_even_an_alternative(self) -> None:
        """A refusal, not a preference: no score compensates for it.

        Both agents fit the role identically, so nothing in the ranking
        separates them. The weak one still does not appear, because at these
        stakes it could not do the work if the strong one were unavailable.
        """
        service = _service(capability=_policy())

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


class TestTheServiceOwnsTheRequirement:
    def test_the_service_stamps_the_requirement_a_caller_omitted(self) -> None:
        """A caller cannot assign consequential work under a weaker bar.

        The strategy is built holding NO policy, so the refusal below can
        only come from the requirement the service stamped onto the request.
        Built with one, this would pass whether or not the service stamped
        anything.
        """
        service = _service(capability=_policy(), strategy_capability=None)

        result = service.assign(_request(Stakes.HIGH, _agent("Weak", "basic")))

        assert result.selected is None

    def test_an_unwired_policy_gates_nothing(self) -> None:
        """An installation with no capability registry still assigns work."""
        service = _service(capability=None)

        result = service.assign(_request(Stakes.CRITICAL, _agent("Weak", "basic")))

        assert result.selected is not None
        assert result.selected.agent_identity.name == "Weak"

    def test_the_registry_rung_decides_not_the_roster_one(self) -> None:
        """A roster row claiming expert does not staff expert work."""
        service = _service(capability=_policy())
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
