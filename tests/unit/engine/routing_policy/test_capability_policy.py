"""The single answer to what a task needs and whether an agent may take it."""

import pytest

from synthorg.core.agent import ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    ResolvedAgentCapabilityReader,
    rank_of,
)
from synthorg.engine.routing_policy.config import (
    CapabilityPolicyConfig,
    StakesCapabilityFloor,
    StakesReasoning,
)
from synthorg.providers.routing.models import ResolvedModel

pytestmark = pytest.mark.unit


class _StubResolver:
    """Answers for exactly the pairs it was given."""

    def __init__(self, models: dict[tuple[str, str], ResolvedModel]) -> None:
        self._models = models

    def resolve_for_pair(self, provider_name: str, ref: str) -> ResolvedModel | None:
        return self._models.get((provider_name, ref))


def _resolved(
    provider: str,
    model_id: str,
    capability: str | None,
) -> ResolvedModel:
    return ResolvedModel(
        provider_name=provider,
        model_id=model_id,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
        capability=capability,  # type: ignore[arg-type]
    )


def _model(provider: str, model_id: str, capability: str | None) -> ModelConfig:
    return ModelConfig(
        provider=provider,
        model_id=model_id,
        capability=capability,  # type: ignore[arg-type]
    )


def _policy(
    config: CapabilityPolicyConfig | None = None,
    **models: str | None,
) -> CapabilityPolicy:
    resolver = _StubResolver(
        {
            ("test-provider", model_id): _resolved(
                "test-provider", model_id, capability
            )
            for model_id, capability in models.items()
        }
    )
    return CapabilityPolicy(
        config=config if config is not None else CapabilityPolicyConfig(),
        reader=ResolvedAgentCapabilityReader(resolver),
    )


class TestRankOf:
    def test_the_ladder_orders_the_rungs(self) -> None:
        assert rank_of("basic") < rank_of("capable") < rank_of("expert")

    def test_an_ungraded_pair_sorts_below_every_rung(self) -> None:
        """Unknown must never outrank a graded pair in any selection."""
        assert rank_of(None) < rank_of("basic")


class TestResolvedAgentCapabilityReader:
    def test_it_reads_the_rung_the_registry_assigned(self) -> None:
        reader = ResolvedAgentCapabilityReader(
            _StubResolver(
                {
                    ("test-provider", "test-expert-001"): _resolved(
                        "test-provider", "test-expert-001", "expert"
                    )
                }
            )
        )

        found = reader.capability_for(
            _model("test-provider", "test-expert-001", "basic")
        )

        assert found == "expert"

    def test_the_registry_outranks_a_stale_roster_rung(self) -> None:
        """The roster rung is written at match time and ages badly.

        An operator override re-grades a model without touching any roster
        row, so the two disagree the moment one is set; the registry is the
        one that moved.
        """
        reader = ResolvedAgentCapabilityReader(
            _StubResolver(
                {
                    ("test-provider", "shared"): _resolved(
                        "test-provider", "shared", "capable"
                    )
                }
            )
        )

        found = reader.capability_for(_model("test-provider", "shared", "expert"))

        assert found == "capable"

    def test_the_roster_stands_in_when_the_pair_is_not_in_the_catalogue(self) -> None:
        reader = ResolvedAgentCapabilityReader(_StubResolver({}))

        found = reader.capability_for(_model("test-provider", "gone", "capable"))

        assert found == "capable"

    def test_a_catalogue_entry_with_no_rung_falls_back_to_the_roster(self) -> None:
        reader = ResolvedAgentCapabilityReader(
            _StubResolver(
                {
                    ("test-provider", "ungraded"): _resolved(
                        "test-provider", "ungraded", None
                    )
                }
            )
        )

        found = reader.capability_for(_model("test-provider", "ungraded", "basic"))

        assert found == "basic"

    def test_neither_source_knowing_reads_as_unknown(self) -> None:
        reader = ResolvedAgentCapabilityReader(_StubResolver({}))

        assert reader.capability_for(_model("test-provider", "gone", None)) is None


class TestRequiredFor:
    def test_stakes_pick_the_required_rung(self) -> None:
        policy = _policy()

        assert policy.required_for(Stakes.LOW) == "basic"
        assert policy.required_for(Stakes.NORMAL) == "capable"
        assert policy.required_for(Stakes.HIGH) == "expert"
        assert policy.required_for(Stakes.CRITICAL) == "expert"

    @pytest.mark.parametrize(
        ("complexity", "expected"),
        [
            (Complexity.SIMPLE, "basic"),
            (Complexity.MEDIUM, "basic"),
            (Complexity.COMPLEX, "capable"),
            (Complexity.EPIC, "capable"),
        ],
    )
    def test_substantial_complexity_raises_the_floor_one_rung(
        self, complexity: Complexity, expected: str
    ) -> None:
        assert _policy().required_for(Stakes.LOW, complexity) == expected

    def test_the_bump_never_climbs_past_the_strongest_rung(self) -> None:
        assert _policy().required_for(Stakes.CRITICAL, Complexity.EPIC) == "expert"

    def test_it_reads_the_operator_configured_floors(self) -> None:
        """Required rungs read the configured floors, never a fresh default."""
        config = CapabilityPolicyConfig(
            capability_floors=StakesCapabilityFloor(
                low="capable", normal="capable", high="capable", critical="expert"
            )
        )

        assert _policy(config).required_for(Stakes.LOW) == "capable"

    def test_a_re_resolved_config_applies_to_the_next_judgement(self) -> None:
        policy = _policy()
        assert policy.required_for(Stakes.LOW) == "basic"

        policy.set_config(
            CapabilityPolicyConfig(
                capability_floors=StakesCapabilityFloor(
                    low="expert", normal="expert", high="expert", critical="expert"
                )
            )
        )

        assert policy.required_for(Stakes.LOW) == "expert"


class TestJudge:
    def test_an_agent_at_the_rung_is_an_exact_match(self) -> None:
        policy = _policy(None, strong="expert")

        verdict = policy.judge(
            model=_model("test-provider", "strong", None), stakes=Stakes.HIGH
        )

        assert verdict.required == "expert"
        assert verdict.agent == "expert"
        assert verdict.fit == "match"
        assert verdict.sanctioned

    def test_an_agent_above_the_rung_fits_higher(self) -> None:
        policy = _policy(None, strong="expert")

        verdict = policy.judge(
            model=_model("test-provider", "strong", None), stakes=Stakes.LOW
        )

        assert verdict.fit == "higher"
        assert verdict.sanctioned

    def test_a_weaker_agent_is_sanctioned_below_the_park_floor(self) -> None:
        """Low and normal stakes take the concession and log it."""
        policy = _policy(None, weak="basic")

        verdict = policy.judge(
            model=_model("test-provider", "weak", None), stakes=Stakes.NORMAL
        )

        assert verdict.fit == "lower"
        assert verdict.sanctioned

    def test_a_weaker_agent_is_refused_at_or_above_the_park_floor(self) -> None:
        policy = _policy(None, weak="basic")

        verdict = policy.judge(
            model=_model("test-provider", "weak", None), stakes=Stakes.HIGH
        )

        assert verdict.fit == "lower"
        assert not verdict.sanctioned

    def test_an_ungraded_pair_is_never_sanctioned(self) -> None:
        """A binding dispatch cannot resolve is not a weak one."""
        policy = _policy(None, weak="basic")

        verdict = policy.judge(
            model=_model("test-provider", "gone", None), stakes=Stakes.LOW
        )

        assert verdict.agent is None
        assert verdict.fit == "lower"
        assert not verdict.sanctioned
        assert verdict.unresolved

    def test_the_registry_rung_decides_not_the_roster_one(self) -> None:
        """A grade in the registry outranks a stale claim on the roster."""
        policy = _policy(None, shared="basic")

        verdict = policy.judge(
            model=_model("test-provider", "shared", "expert"), stakes=Stakes.HIGH
        )

        assert verdict.agent == "basic"
        assert not verdict.sanctioned

    def test_the_park_floor_is_operator_configurable(self) -> None:
        policy = _policy(
            CapabilityPolicyConfig(park_min_stakes=Stakes.LOW), weak="basic"
        )

        verdict = policy.judge(
            model=_model("test-provider", "weak", None), stakes=Stakes.NORMAL
        )

        assert not verdict.sanctioned


class TestParksWhenLower:
    def test_it_parks_at_and_above_the_configured_floor(self) -> None:
        policy = _policy()

        assert not policy.parks_when_lower(Stakes.LOW)
        assert not policy.parks_when_lower(Stakes.NORMAL)
        assert policy.parks_when_lower(Stakes.HIGH)
        assert policy.parks_when_lower(Stakes.CRITICAL)


class TestReasoningEffort:
    def test_the_shipped_ladder_deepens_with_stakes(self) -> None:
        policy = _policy()

        assert policy.reasoning_effort(Stakes.LOW) is None
        assert policy.reasoning_effort(Stakes.NORMAL) is ReasoningEffort.LOW
        assert policy.reasoning_effort(Stakes.HIGH) is ReasoningEffort.MEDIUM
        assert policy.reasoning_effort(Stakes.CRITICAL) is ReasoningEffort.HIGH

    def test_it_reads_the_operator_configured_depths(self) -> None:
        config = CapabilityPolicyConfig(
            reasoning=StakesReasoning(
                low=None,
                normal=None,
                high=ReasoningEffort.LOW,
                critical=ReasoningEffort.MEDIUM,
            )
        )

        assert _policy(config).reasoning_effort(Stakes.HIGH) is ReasoningEffort.LOW


class TestRedTeamRequired:
    def test_it_fires_at_and_above_the_configured_floor(self) -> None:
        policy = _policy()

        assert not policy.red_team_required(Stakes.NORMAL)
        assert policy.red_team_required(Stakes.HIGH)
        assert policy.red_team_required(Stakes.CRITICAL)

    def test_lowering_the_floor_widens_what_gets_attacked(self) -> None:
        policy = _policy(CapabilityPolicyConfig(red_team_min_stakes=Stakes.LOW))

        assert policy.red_team_required(Stakes.LOW)
