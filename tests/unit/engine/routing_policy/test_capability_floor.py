"""The single answer to what a task needs and what an agent is."""

import pytest

from synthorg.core.agent import ModelConfig
from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.capability_floor import (
    CapabilityFloorPolicy,
    ResolvedAgentCapabilityReader,
    clears_floor,
)
from synthorg.engine.routing_policy.config import StakesCapabilityFloor
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


class TestClearsFloor:
    def test_a_stronger_rung_clears_a_weaker_requirement(self) -> None:
        assert clears_floor("expert", "capable")

    def test_the_same_rung_clears_its_own_requirement(self) -> None:
        assert clears_floor("capable", "capable")

    def test_a_weaker_rung_does_not_clear(self) -> None:
        assert not clears_floor("basic", "capable")

    def test_no_requirement_is_cleared_by_anything(self) -> None:
        """Flat routing and an unwired policy both mean 'do not gate'."""
        assert clears_floor("basic", None)
        assert clears_floor(None, None)

    def test_an_ungraded_model_clears_nothing(self) -> None:
        """Unknown is not a rung, and consequential work is not a gamble.

        A pair no configured provider serves and no roster rung describes
        cannot be reasoned about at all, and it is also a pair the dispatch
        cannot resolve, so refusing here names the problem earlier.
        """
        assert not clears_floor(None, "basic")
        assert not clears_floor(None, "expert")


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


class TestCapabilityFloorPolicy:
    def _policy(self, **models: str | None) -> CapabilityFloorPolicy:
        resolver = _StubResolver(
            {
                ("test-provider", model_id): _resolved(
                    "test-provider", model_id, capability
                )
                for model_id, capability in models.items()
            }
        )
        return CapabilityFloorPolicy(
            floors=StakesCapabilityFloor(),
            reader=ResolvedAgentCapabilityReader(resolver),
        )

    def test_stakes_pick_the_required_rung(self) -> None:
        policy = self._policy()

        assert policy.required_for(Stakes.LOW) == "basic"
        assert policy.required_for(Stakes.NORMAL) == "capable"
        assert policy.required_for(Stakes.HIGH) == "expert"
        assert policy.required_for(Stakes.CRITICAL) == "expert"

    def test_an_agent_at_the_rung_clears_it(self) -> None:
        policy = self._policy(strong="expert")

        assert policy.clears(_model("test-provider", "strong", None), "expert")

    def test_an_agent_below_the_rung_does_not(self) -> None:
        policy = self._policy(weak="basic")

        assert not policy.clears(_model("test-provider", "weak", None), "expert")

    def test_the_registry_rung_decides_not_the_roster_one(self) -> None:
        """The defect this exists to stop: a roster claim deciding routing."""
        policy = self._policy(shared="basic")

        assert not policy.clears(_model("test-provider", "shared", "expert"), "expert")
