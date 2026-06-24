"""Tests for capability-demand model selection (tiering + domination)."""

from collections import Counter

import pytest

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.templates.model_matcher import match_all_agents
from synthorg.templates.model_matcher_tiering import (
    demand_tier,
    prune_dominated,
    rank_by_quality,
    select_for_demand,
)
from synthorg.templates.model_requirements import ModelPriority, ModelRequirement


def _model(  # noqa: PLR0913 -- keyword-only test factory
    model_id: str,
    *,
    cost_tier: int | None = None,
    family: str | None = None,
    generation: float | None = None,
    parameter_count: int | None = None,
    max_context: int = 200_000,
    tools: bool = True,
    reasoning: bool = True,
    source: MetadataSource = "probe",
) -> ProviderModelConfig:
    """Build a ProviderModelConfig with the metadata the tiering reads."""
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=0.0,
        max_context=max_context,
        metadata=ModelMetadata(
            supports_tools=tools,
            supports_reasoning=reasoning,
            parameter_count=parameter_count,
            cost_tier=cost_tier,
            family=family,
            generation=generation,
            metadata_source=source,
        ),
    )


class _Provider:
    def __init__(self, *models: ProviderModelConfig) -> None:
        self.models = models


@pytest.mark.unit
class TestDemandTier:
    @pytest.mark.parametrize(
        ("priority", "reasoning", "expected"),
        [
            ("quality", True, 4),
            ("quality", False, 3),
            ("balanced", True, 3),
            ("balanced", False, 2),
            ("cost", False, 1),
            ("speed", False, 1),
        ],
    )
    def test_maps_demand_to_tier(
        self, priority: ModelPriority, reasoning: bool, expected: int
    ) -> None:
        req = ModelRequirement(priority=priority, requires_reasoning=reasoning)
        assert demand_tier(req) == expected


@pytest.mark.unit
class TestPruneDominated:
    def test_drops_older_sibling_in_same_tier(self) -> None:
        # glm-5.2 (tier 3, newer) dominates glm-4.7 (tier 3, older) -> drop 4.7.
        newer = _model("glm-5.2", cost_tier=3, family="glm", generation=5.2)
        older = _model("glm-4.7", cost_tier=3, family="glm", generation=4.7)
        kept = {m.id for m in prune_dominated([older, newer])}
        assert kept == {"glm-5.2"}

    def test_collapses_redundant_family_versions(self) -> None:
        kimis = [
            _model("kimi-k2.5", cost_tier=3, family="kimi", generation=2.5),
            _model("kimi-k2.6", cost_tier=3, family="kimi", generation=2.6),
            _model("kimi-k2.7", cost_tier=3, family="kimi", generation=2.7),
        ]
        kept = {m.id for m in prune_dominated(kimis)}
        assert kept == {"kimi-k2.7"}

    def test_keeps_distinct_families_in_one_tier(self) -> None:
        models = [
            _model("glm-5.2", cost_tier=3, family="glm", generation=5.2),
            _model("kimi-k2.7", cost_tier=3, family="kimi", generation=2.7),
        ]
        assert len(prune_dominated(models)) == 2

    def test_keeps_same_family_in_different_tiers(self) -> None:
        # A cheaper sibling in a lower tier is a valid budget option, not dominated.
        models = [
            _model("glm-5.2", cost_tier=4, family="glm", generation=5.2),
            _model("glm-mini", cost_tier=2, family="glm", generation=5.0),
        ]
        assert len(prune_dominated(models)) == 2

    def test_passes_through_unclassifiable(self) -> None:
        model = _model("mystery", cost_tier=None, family=None)
        assert prune_dominated([model]) == [model]


@pytest.mark.unit
class TestSelectForDemand:
    def test_picks_nearest_available_tier(self) -> None:
        heavy = _model("heavy", cost_tier=4, family="a")
        light = _model("light", cost_tier=2, family="b")
        assert select_for_demand([heavy, light], 4, Counter()) is heavy
        # target 1: light (dist 1) beats heavy (dist 3).
        assert select_for_demand([heavy, light], 1, Counter()) is light

    def test_spreads_across_families_in_a_tier(self) -> None:
        alpha = _model("a1", cost_tier=3, family="alpha")
        beta = _model("b1", cost_tier=3, family="beta")
        usage: Counter[str] = Counter()
        first = select_for_demand([alpha, beta], 3, usage)
        assert first is not None
        assert first.metadata.family is not None
        usage[first.metadata.family] += 1
        second = select_for_demand([alpha, beta], 3, usage)
        assert second is not None
        assert second.metadata.family != first.metadata.family

    def test_empty_returns_none(self) -> None:
        assert select_for_demand([], 3, Counter()) is None


@pytest.mark.unit
class TestRankByQuality:
    def test_orders_by_generation_then_size(self) -> None:
        ranked = rank_by_quality(
            [
                _model("old-big", generation=3.0, parameter_count=500),
                _model("new-small", generation=5.0, parameter_count=100),
            ]
        )
        assert ranked[0].id == "new-small"


@pytest.mark.unit
class TestMatchAllAgentsDemandDriven:
    def test_hard_work_outranks_routine_regardless_of_order(self) -> None:
        providers = {
            "cloud": _Provider(
                _model("frontier", cost_tier=4, family="deep", generation=4.0),
                _model("mid", cost_tier=2, family="gem", generation=4.0),
                _model("cheap", cost_tier=1, family="min", generation=3.0),
            )
        }
        # Routine role listed first; the demanding role must still claim the
        # heavy model (demand-order assignment).
        agents: list[dict[str, object]] = [
            {"role": "QA", "model_requirement": {"priority": "cost"}},
            {
                "role": "Researcher",
                "model_requirement": {
                    "priority": "quality",
                    "requires_reasoning": True,
                },
            },
        ]
        matches = match_all_agents(agents, providers)
        by_index = {m.agent_index: m.model_id for m in matches}
        assert by_index[1] == "frontier"
        assert by_index[0] == "cheap"

    def test_dominated_model_is_never_assigned(self) -> None:
        providers = {
            "cloud": _Provider(
                _model("glm-5.2", cost_tier=3, family="glm", generation=5.2),
                _model("glm-4.7", cost_tier=3, family="glm", generation=4.7),
            )
        }
        agents: list[dict[str, object]] = [
            {"role": "A", "model_requirement": {"priority": "quality"}},
            {"role": "B", "model_requirement": {"priority": "quality"}},
        ]
        chosen = {m.model_id for m in match_all_agents(agents, providers)}
        assert "glm-4.7" not in chosen
        assert chosen == {"glm-5.2"}
