"""Tests for the capability-aware model-matching engine."""

from datetime import date

import pytest

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.templates.model_matcher import (
    CapabilityFitStrategy,
    ModelMatch,
    ModelSelectionStrategy,
    get_model_selection_strategy,
    match_all_agents,
    match_model,
)
from synthorg.templates.model_matcher_config import ModelMatcherConfig, derive_tier
from synthorg.templates.model_requirements import ModelRequirement

_CFG = ModelMatcherConfig()


def _make_model(  # noqa: PLR0913 -- keyword-only test factory
    model_id: str,
    *,
    max_context: int = 200_000,
    cost_input: float = 0.01,
    latency_ms: int | None = None,
    tools: bool = False,
    vision: bool = False,
    reasoning: bool = False,
    family: str | None = None,
    generation: float | None = None,
    release_date: date | None = None,
    source: MetadataSource = "litellm",
) -> ProviderModelConfig:
    """Factory for test ProviderModelConfig with metadata."""
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=cost_input,
        max_context=max_context,
        estimated_latency_ms=latency_ms,
        metadata=ModelMetadata(
            supports_tools=tools,
            supports_vision=vision,
            supports_reasoning=reasoning,
            family=family,
            generation=generation,
            release_date=release_date,
            metadata_source=source,
        ),
    )


class _Provider:
    """Minimal provider exposing a typed ``models`` tuple."""

    def __init__(self, models: tuple[ProviderModelConfig, ...]) -> None:
        self.models = models


def _provider(*models: ProviderModelConfig) -> _Provider:
    """Build a ``_Provider`` from positional models."""
    return _Provider(models)


# ── Hard capability filters ──────────────────────────────────


@pytest.mark.unit
class TestHardFilters:
    def test_vision_requirement_excludes_non_vision(self) -> None:
        no_vision = _make_model("plain", vision=False)
        has_vision = _make_model("seer", vision=True)
        req = ModelRequirement(requires_vision=True)
        model, _ = match_model(req, (no_vision, has_vision))
        assert model is not None
        assert model.id == "seer"

    def test_tools_requirement_excludes_non_tools(self) -> None:
        req = ModelRequirement(requires_tools=True)
        model, score = match_model(req, (_make_model("plain", tools=False),))
        assert model is None
        assert score == 0.0

    def test_reasoning_requirement_honoured(self) -> None:
        thinker = _make_model("thinker", reasoning=True)
        plain = _make_model("plain", reasoning=False)
        req = ModelRequirement(requires_reasoning=True)
        model, _ = match_model(req, (plain, thinker))
        assert model is not None
        assert model.id == "thinker"

    def test_min_context_filter(self) -> None:
        small = _make_model("small", max_context=8_000)
        big = _make_model("big", max_context=200_000)
        req = ModelRequirement(min_context=100_000)
        model, _ = match_model(req, (small, big))
        assert model is not None
        assert model.id == "big"

    def test_unknown_metadata_fails_closed_when_required(self) -> None:
        # Model claims no capabilities AND its metadata is unenriched.
        unknown = _make_model("legacy", vision=False, source="unknown")
        req = ModelRequirement(requires_vision=True)
        model, score = match_model(req, (unknown,))
        assert model is None
        assert score == 0.0

    def test_unknown_metadata_ok_when_not_required(self) -> None:
        unknown = _make_model("legacy", source="unknown")
        req = ModelRequirement()
        model, _ = match_model(req, (unknown,))
        assert model is not None
        assert model.id == "legacy"


# ── Family / pattern resolution ──────────────────────────────


@pytest.mark.unit
class TestFamilyResolution:
    def test_family_pins_newest_generation(self) -> None:
        old = _make_model("ex-1", family="example-large", generation=1.0)
        new = _make_model("ex-2", family="example-large", generation=2.0)
        other = _make_model("other", family="example-small", generation=9.0)
        req = ModelRequirement(family="example-large")
        model, _ = match_model(req, (old, new, other))
        assert model is not None
        assert model.id == "ex-2"

    def test_family_never_crosses_failed_capability_gate(self) -> None:
        # Newest in family lacks vision; older one has it.
        newest_no_vision = _make_model(
            "ex-2", family="example-large", generation=2.0, vision=False
        )
        older_vision = _make_model(
            "ex-1", family="example-large", generation=1.0, vision=True
        )
        req = ModelRequirement(family="example-large", requires_vision=True)
        model, _ = match_model(req, (newest_no_vision, older_vision))
        assert model is not None
        assert model.id == "ex-1"

    def test_pattern_pins_newest_matching_id(self) -> None:
        a = _make_model("example-x-1", generation=1.0)
        b = _make_model("example-x-2", generation=2.0)
        c = _make_model("other-9", generation=9.0)
        req = ModelRequirement(model_pattern="example-x-*")
        model, _ = match_model(req, (a, b, c))
        assert model is not None
        assert model.id == "example-x-2"

    def test_family_miss_falls_back_to_survivors(self) -> None:
        m = _make_model("only", family="example-large", generation=1.0)
        req = ModelRequirement(family="nonexistent-family")
        model, score = match_model(req, (m,))
        assert model is not None
        assert model.id == "only"
        assert score > 0.0

    def test_newest_breaks_generation_tie_by_release_date(self) -> None:
        older = _make_model(
            "ex-a", family="f", generation=2.0, release_date=date(2025, 1, 1)
        )
        newer = _make_model(
            "ex-b", family="f", generation=2.0, release_date=date(2025, 6, 1)
        )
        req = ModelRequirement(family="f")
        model, _ = match_model(req, (older, newer))
        assert model is not None
        assert model.id == "ex-b"


# ── Priority axis (absolute, not cost-thirds) ────────────────


@pytest.mark.unit
class TestPriorityAxis:
    def test_cost_priority_picks_cheapest(self) -> None:
        cheap = _make_model("cheap", cost_input=0.001)
        mid = _make_model("mid", cost_input=0.01)
        dear = _make_model("dear", cost_input=0.1)
        req = ModelRequirement(priority="cost")
        model, _ = match_model(req, (dear, mid, cheap))
        assert model is not None
        assert model.id == "cheap"

    def test_quality_priority_picks_newest_generation(self) -> None:
        g1 = _make_model("g1", generation=1.0)
        g3 = _make_model("g3", generation=3.0)
        req = ModelRequirement(priority="quality")
        model, _ = match_model(req, (g1, g3))
        assert model is not None
        assert model.id == "g3"

    def test_speed_priority_picks_lowest_latency(self) -> None:
        slow = _make_model("slow", latency_ms=2_000)
        fast = _make_model("fast", latency_ms=200)
        req = ModelRequirement(priority="speed")
        model, _ = match_model(req, (slow, fast))
        assert model is not None
        assert model.id == "fast"

    def test_speed_priority_deprioritises_unknown_latency(self) -> None:
        slow = _make_model("slow", latency_ms=5_000)
        unknown = _make_model("unknown", latency_ms=None)
        req = ModelRequirement(priority="speed")
        model, _ = match_model(req, (unknown, slow))
        # A model with a real (even slow) latency beats unknown latency.
        assert model is not None
        assert model.id == "slow"

    def test_balanced_priority_blends_quality_and_cost(self) -> None:
        # Pool-normalised blend: the mid model beats both the dear-but-newest
        # and the cheap-but-oldest extremes (which tie). Without normalisation
        # the raw gen-minus-cost formula would just pick the newest.
        quality = _make_model("dear-new", generation=3.0, cost_input=0.1)
        cheap = _make_model("cheap-old", generation=1.0, cost_input=0.001)
        middle = _make_model("mid", generation=2.0, cost_input=0.01)
        req = ModelRequirement(priority="balanced")
        model, _ = match_model(req, (quality, cheap, middle))
        assert model is not None
        assert model.id == "mid"


# ── Derived tier + score bounds ──────────────────────────────


@pytest.mark.unit
class TestDeriveTierAndScore:
    def test_derive_tier_bands(self) -> None:
        assert derive_tier(_make_model("a", max_context=200_000), _CFG) == "large"
        assert derive_tier(_make_model("b", max_context=64_000), _CFG) == "medium"
        assert derive_tier(_make_model("c", max_context=8_000), _CFG) == "small"

    def test_score_within_bounds(self) -> None:
        models = (
            _make_model("a", tools=True, vision=True, reasoning=True),
            _make_model("b"),
        )
        _, score = match_model(ModelRequirement(min_context=1_000), models)
        assert 0.0 <= score <= 1.0

    def test_empty_available_returns_none(self) -> None:
        model, score = match_model(ModelRequirement(), ())
        assert model is None
        assert score == 0.0


# ── Batch matching ───────────────────────────────────────────


@pytest.mark.unit
class TestMatchAllAgents:
    def test_assigns_and_derives_tier_from_selected_model(self) -> None:
        providers = {"prov": _provider(_make_model("big", max_context=200_000))}
        agents = [{"tier": "small"}]
        matches = match_all_agents(agents, providers)
        assert len(matches) == 1
        assert matches[0].model_id == "big"
        # Tier reflects the SELECTED model, not the requested "small".
        assert matches[0].tier == "large"

    def test_omits_agent_when_no_capability_match(self) -> None:
        # Requires vision but no provider model has it -> fail-closed: the
        # agent is omitted rather than assigned a non-compliant model.
        providers = {"prov": _provider(_make_model("plain", vision=False))}
        agents = [{"model_requirement": {"requires_vision": True}}]
        matches = match_all_agents(agents, providers)
        assert matches == []

    def test_selects_compliant_model_in_another_provider(self) -> None:
        # The first provider's model fails the hard filter; the matcher
        # still finds the compliant model in the second provider.
        providers = {
            "alpha": _provider(_make_model("alpha-1", vision=False)),
            "beta": _provider(_make_model("beta-1", vision=True)),
        }
        agents = [{"model_requirement": {"requires_vision": True}}]
        matches = match_all_agents(agents, providers)
        assert len(matches) == 1
        assert matches[0].provider_name == "beta"
        assert matches[0].model_id == "beta-1"

    def test_no_models_anywhere_omits_agent(self) -> None:
        providers = {"prov": _provider()}
        matches = match_all_agents([{"tier": "medium"}], providers)
        assert matches == []

    def test_returns_model_match_instances(self) -> None:
        providers = {"prov": _provider(_make_model("m"))}
        matches = match_all_agents([{"tier": "medium"}], providers)
        assert all(isinstance(m, ModelMatch) for m in matches)


# ── Strategy seam ────────────────────────────────────────────


@pytest.mark.unit
class TestStrategySeam:
    def test_default_strategy_is_capability_fit(self) -> None:
        assert isinstance(get_model_selection_strategy(), CapabilityFitStrategy)

    def test_default_strategy_satisfies_protocol(self) -> None:
        assert isinstance(get_model_selection_strategy(), ModelSelectionStrategy)

    def test_custom_strategy_is_used(self) -> None:
        sentinel = _make_model("sentinel")

        class _FixedStrategy:
            def select(
                self,
                requirement: ModelRequirement,
                candidates: object,
                config: ModelMatcherConfig,
            ) -> tuple[ProviderModelConfig, float]:
                return sentinel, 1.0

        model, score = match_model(
            ModelRequirement(requires_vision=True),
            (_make_model("ignored"),),
            strategy=_FixedStrategy(),
        )
        assert model is sentinel
        assert score == 1.0


# ── Config projection ────────────────────────────────────────


@pytest.mark.unit
class TestModelMatcherConfig:
    def test_from_bridge_config_projects_fields(self) -> None:
        from synthorg.settings.bridge_configs import EngineBridgeConfig

        cfg = ModelMatcherConfig.from_bridge_config(EngineBridgeConfig())
        assert cfg.base_score == EngineBridgeConfig().matcher_base_score
        assert (
            cfg.tier_large_min_context
            == EngineBridgeConfig().matcher_tier_large_min_context
        )
