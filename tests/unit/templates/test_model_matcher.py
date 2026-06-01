"""Tests for the tier-to-model matching engine."""

import pytest

from synthorg.config.schema import ProviderModelConfig
from synthorg.templates.model_matcher import (
    ModelMatch,
    ModelMatcherConfig,
    _classify_tiers,
    _rank_by_priority,
    match_all_agents,
    match_model,
)
from synthorg.templates.model_requirements import ModelRequirement


def _make_model(
    model_id: str,
    cost_input: float = 0.01,
    cost_output: float = 0.02,
    max_context: int = 200_000,
    latency_ms: int | None = None,
) -> ProviderModelConfig:
    """Factory for test ProviderModelConfig instances."""
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=cost_input,
        cost_per_1k_output=cost_output,
        max_context=max_context,
        estimated_latency_ms=latency_ms,
    )


# ── Tier classification ──────────────────────────────────────


@pytest.mark.unit
class TestClassifyTiers:
    def test_single_model_all_tiers(self) -> None:
        model = _make_model("only-one", cost_input=0.01)
        tiers = _classify_tiers([model])
        assert model in tiers["large"]
        assert model in tiers["medium"]
        assert model in tiers["small"]

    def test_two_models_all_tiers(self) -> None:
        cheap = _make_model("cheap", cost_input=0.001)
        expensive = _make_model("expensive", cost_input=0.1)
        tiers = _classify_tiers([cheap, expensive])
        # Both appear in ALL tiers since < 3 models.
        assert cheap in tiers["small"]
        assert cheap in tiers["medium"]
        assert cheap in tiers["large"]
        assert expensive in tiers["small"]
        assert expensive in tiers["medium"]
        assert expensive in tiers["large"]

    def test_three_models_split(self) -> None:
        s = _make_model("small-m", cost_input=0.001)
        m = _make_model("medium-m", cost_input=0.01)
        lg = _make_model("large-m", cost_input=0.1)
        tiers = _classify_tiers([lg, s, m])  # Unordered input.
        assert s in tiers["small"]
        assert m in tiers["medium"]
        assert lg in tiers["large"]

    def test_six_models_even_split(self) -> None:
        models = [_make_model(f"m{i}", cost_input=i * 0.01) for i in range(1, 7)]
        tiers = _classify_tiers(models)
        assert len(tiers["small"]) == 2
        assert len(tiers["medium"]) == 2
        assert len(tiers["large"]) == 2


# ── Rank by priority ─────────────────────────────────────────


@pytest.mark.unit
class TestRankByPriority:
    def test_quality_picks_most_expensive(self) -> None:
        models = [
            _make_model("cheap", cost_input=0.001),
            _make_model("pricey", cost_input=0.1),
        ]
        result = _rank_by_priority(models, "quality")
        assert result.id == "pricey"

    def test_cost_picks_cheapest(self) -> None:
        models = [
            _make_model("cheap", cost_input=0.001),
            _make_model("pricey", cost_input=0.1),
        ]
        result = _rank_by_priority(models, "cost")
        assert result.id == "cheap"

    def test_speed_picks_lowest_latency(self) -> None:
        models = [
            _make_model("slow", cost_input=0.01, latency_ms=500),
            _make_model("fast", cost_input=0.01, latency_ms=50),
        ]
        result = _rank_by_priority(models, "speed")
        assert result.id == "fast"

    def test_speed_with_no_latency_data(self) -> None:
        models = [
            _make_model("no-latency", cost_input=0.01),
            _make_model("has-latency", cost_input=0.01, latency_ms=100),
        ]
        result = _rank_by_priority(models, "speed")
        assert result.id == "has-latency"

    def test_balanced_picks_mid_range(self) -> None:
        models = [
            _make_model("cheap", cost_input=0.001),
            _make_model("mid", cost_input=0.05),
            _make_model("pricey", cost_input=0.1),
        ]
        result = _rank_by_priority(models, "balanced")
        assert result.id == "mid"


# ── match_model ──────────────────────────────────────────────


@pytest.mark.unit
class TestMatchModel:
    def test_no_models_returns_none(self) -> None:
        req = ModelRequirement(tier="medium")
        model, score = match_model(req, ())
        assert model is None
        assert score == 0.0

    def test_single_model_always_matches(self) -> None:
        req = ModelRequirement(tier="large")
        only = _make_model("only-one", cost_input=0.01)
        model, score = match_model(req, (only,))
        assert model is not None
        assert model.id == "only-one"
        assert score > 0.0

    def test_min_context_filters(self) -> None:
        req = ModelRequirement(tier="medium", min_context=500_000)
        small_ctx = _make_model("small-ctx", max_context=100_000)
        model, score = match_model(req, (small_ctx,))
        assert model is None
        assert score == 0.0

    def test_min_context_passes(self) -> None:
        req = ModelRequirement(tier="medium", min_context=100_000)
        big_ctx = _make_model("big-ctx", max_context=200_000)
        model, _score = match_model(req, (big_ctx,))
        assert model is not None

    def test_tier_preference(self) -> None:
        req = ModelRequirement(tier="large", priority="quality")
        models = tuple(_make_model(f"m{i}", cost_input=i * 0.01) for i in range(1, 7))
        model, _score = match_model(req, models)
        assert model is not None
        # Should pick from the large tier (most expensive).
        assert model.cost_per_1k_input >= 0.04

    def test_tier_fallback_when_exact_empty(self) -> None:
        """When all models have similar cost, tiers overlap."""
        req = ModelRequirement(tier="large")
        model = _make_model("only", cost_input=0.01)
        result, _score = match_model(req, (model,))
        assert result is not None

    def test_score_range(self) -> None:
        req = ModelRequirement(tier="medium")
        model = _make_model("test", cost_input=0.01)
        _, score = match_model(req, (model,))
        assert 0.0 <= score <= 1.0


# ── match_all_agents ─────────────────────────────────────────


class _FakeProviderConfig:
    """Minimal stand-in for ProviderConfig with a models attribute."""

    def __init__(self, models: tuple[ProviderModelConfig, ...]) -> None:
        self.models = models


@pytest.mark.unit
class TestMatchAllAgents:
    def test_empty_agents(self) -> None:
        results = match_all_agents([], {})
        assert results == []

    def test_single_agent_single_provider(self) -> None:
        agents = [{"tier": "medium", "personality_preset": "pragmatic_builder"}]
        providers = {
            "test-provider": _FakeProviderConfig(
                models=(_make_model("test-model", cost_input=0.01),),
            ),
        }
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        assert isinstance(results[0], ModelMatch)
        assert results[0].provider_name == "test-provider"
        assert results[0].model_id == "test-model"

    def test_multiple_agents_matched(self) -> None:
        agents = [
            {"tier": "large", "personality_preset": "visionary_leader"},
            {"tier": "small", "personality_preset": "eager_learner"},
        ]
        models = (
            _make_model("cheap", cost_input=0.001),
            _make_model("mid", cost_input=0.01),
            _make_model("expensive", cost_input=0.1),
        )
        providers = {"test-provider": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        assert len(results) == 2
        # Large tier agent should get a more expensive model.
        assert results[0].model_id != results[1].model_id

    def test_no_providers_returns_empty(self) -> None:
        agents = [{"tier": "medium"}]
        results = match_all_agents(agents, {})
        assert results == []

    def test_fallback_when_no_tier_match(self) -> None:
        agents = [{"tier": "large", "personality_preset": None}]
        # Single cheap model, large tier requested.
        providers = {
            "test-provider": _FakeProviderConfig(
                models=(_make_model("only", cost_input=0.001),),
            ),
        }
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        # Should still get assigned (fallback or single-model-all-tiers).
        assert results[0].model_id == "only"

    def test_fallback_when_min_context_unsatisfied(self) -> None:
        """Agent whose preset demands more context than any model offers
        still gets a fallback match with score=0."""
        # visionary_leader preset has min_context=100_000 via affinity.
        # Provide a model with only 50k context to force match_model to
        # return None (all candidates filtered out), triggering fallback.
        agents = [
            {
                "tier": "large",
                "personality_preset": "visionary_leader",
            },
        ]
        providers = {
            "test-provider": _FakeProviderConfig(
                models=(_make_model("small-ctx", max_context=50_000),),
            ),
        }
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        assert results[0].model_id == "small-ctx"
        assert results[0].provider_name == "test-provider"
        assert results[0].score == 0.0

    def test_fallback_emits_debug_event_not_warning(self) -> None:
        """Tier-fallback path logs at DEBUG, not WARNING.

        Per the design contract, falling back to ``all_models[0]`` when
        the requested tier has no candidate is the documented happy-path
        and should not pollute WARNING. The dedicated event is
        ``template.model_match.fallback`` (DEBUG); only the
        ``no_models_available`` branch keeps WARNING (which the wizard
        provider gate now prevents from firing in practice).
        """
        import structlog.testing

        agents = [
            {
                "tier": "large",
                "personality_preset": "visionary_leader",
            },
        ]
        providers = {
            "test-provider": _FakeProviderConfig(
                models=(_make_model("small-ctx", max_context=50_000),),
            ),
        }
        with structlog.testing.capture_logs() as logs:
            results = match_all_agents(agents, providers)
        assert len(results) == 1
        fallback_events = [
            log for log in logs if log.get("event") == "template.model_match.fallback"
        ]
        warning_events = [
            log
            for log in logs
            if log.get("event") == "template.model_match.failed"
            and log.get("log_level") == "warning"
        ]
        # Fallback succeeded -- DEBUG event present, WARNING absent.
        assert len(fallback_events) == 1
        assert fallback_events[0]["log_level"] == "debug"
        assert fallback_events[0]["fallback_provider"] == "test-provider"
        assert fallback_events[0]["fallback_model"] == "small-ctx"
        assert warning_events == []

    def test_no_models_available_still_logs_warning(self) -> None:
        """The truly-empty case keeps WARNING severity.

        When ``all_models`` is empty (no providers OR all providers have
        zero models), the matcher cannot assign anything -- this is the
        operator-actionable case the wizard's tier-coverage gate now
        catches before it gets here, but the WARNING should still fire
        if the gate is bypassed (e.g. in a non-wizard call site).
        """
        import structlog.testing

        agents = [{"tier": "large", "personality_preset": None}]
        providers = {
            "empty-provider": _FakeProviderConfig(models=()),
        }
        with structlog.testing.capture_logs() as logs:
            results = match_all_agents(agents, providers)
        assert results == []
        no_models_events = [
            log
            for log in logs
            if log.get("event") == "template.model_match.failed"
            and log.get("reason") == "no_models_available"
        ]
        assert len(no_models_events) == 1
        assert no_models_events[0]["log_level"] == "warning"

    def test_non_str_tier_coerced_to_medium_with_warning(self) -> None:
        """A non-string ``tier`` is coerced to "medium" and warned, not crashed."""
        import structlog.testing

        agents: list[dict[str, object]] = [{"tier": 123}]
        providers = {
            "p": _FakeProviderConfig(models=(_make_model("m1", cost_input=0.01),)),
        }
        with structlog.testing.capture_logs() as logs:
            results = match_all_agents(agents, providers)
        assert len(results) == 1
        assert results[0].tier == "medium"
        coerced = [
            log
            for log in logs
            if log.get("event") == "template.model_match.coerced"
            and log.get("field") == "tier"
        ]
        assert len(coerced) == 1
        assert coerced[0]["log_level"] == "warning"
        assert coerced[0]["coerced_to"] == "medium"

    def test_non_str_preset_ignored_with_warning(self) -> None:
        """A non-string ``personality_preset`` is dropped and warned."""
        import structlog.testing

        agents: list[dict[str, object]] = [
            {"tier": "medium", "personality_preset": ["not", "a", "string"]},
        ]
        providers = {
            "p": _FakeProviderConfig(models=(_make_model("m1", cost_input=0.01),)),
        }
        with structlog.testing.capture_logs() as logs:
            results = match_all_agents(agents, providers)
        assert len(results) == 1
        coerced = [
            log
            for log in logs
            if log.get("event") == "template.model_match.coerced"
            and log.get("field") == "personality_preset"
        ]
        assert len(coerced) == 1
        assert coerced[0]["log_level"] == "warning"

    def test_agent_index_preserved(self) -> None:
        agents = [
            {"tier": "small"},
            {"tier": "large"},
            {"tier": "medium"},
        ]
        models = (_make_model("m1", cost_input=0.01),)
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        for i, result in enumerate(results):
            assert result.agent_index == i

    def test_model_requirement_dict_used_when_present(self) -> None:
        """Serialized ModelRequirement dict bypasses resolve_model_requirement."""
        req_dict = {
            "tier": "large",
            "priority": "quality",
            "min_context": 100_000,
            "capabilities": [],
        }
        agents = [{"tier": "large", "model_requirement": req_dict}]
        models = (
            _make_model("big", cost_input=0.1, max_context=200_000),
            _make_model("small", cost_input=0.001, max_context=200_000),
        )
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        assert results[0].tier == "large"
        assert results[0].model_id == "big"

    def test_invalid_model_requirement_dict_skipped(self) -> None:
        """Malformed model_requirement dict logs warning and skips agent."""
        agents = [{"model_requirement": {"tier": "invalid"}}]
        providers = {
            "p": _FakeProviderConfig(
                models=(_make_model("m1", cost_input=0.01),),
            ),
        }
        results = match_all_agents(agents, providers)
        assert results == []

    def test_model_requirement_min_context_filtering(self) -> None:
        """ModelRequirement min_context filters out small-context models."""
        req_dict = {
            "tier": "medium",
            "priority": "balanced",
            "min_context": 150_000,
            "capabilities": [],
        }
        agents = [{"model_requirement": req_dict}]
        models = (_make_model("too-small", cost_input=0.01, max_context=100_000),)
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        # Should fallback (score 0) since no model meets min_context.
        assert len(results) == 1
        assert results[0].score == 0.0
        assert results[0].model_id == "too-small"

    def test_model_requirement_overrides_affinity(self) -> None:
        """Structured ModelRequirement ignores personality_preset affinity."""
        req_dict = {"tier": "medium", "priority": "cost", "capabilities": []}
        agents = [
            {
                "tier": "medium",
                "personality_preset": "visionary_leader",
                "model_requirement": req_dict,
            },
        ]
        models = (
            _make_model("cheap", cost_input=0.001),
            _make_model("expensive", cost_input=0.1),
        )
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        # Should pick cheap model (priority=cost), NOT expensive
        # (which visionary_leader affinity would select via quality).
        assert results[0].model_id == "cheap"

    def test_model_requirement_object_used_directly(self) -> None:
        """ModelRequirement object (not serialized) is also accepted."""
        req = ModelRequirement(tier="small", priority="speed")
        agents = [{"model_requirement": req}]
        models = (
            _make_model("fast", cost_input=0.01, latency_ms=50),
            _make_model("slow", cost_input=0.01, latency_ms=500),
        )
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        assert results[0].tier == "small"
        assert results[0].model_id == "fast"

    def test_fallback_to_tier_preset_without_model_requirement(self) -> None:
        """Without model_requirement, the old tier+preset path is used."""
        agents = [
            {"tier": "large", "personality_preset": "visionary_leader"},
        ]
        models = (
            _make_model("cheap", cost_input=0.001, max_context=200_000),
            _make_model("mid", cost_input=0.05, max_context=200_000),
            _make_model("expensive", cost_input=0.1, max_context=200_000),
        )
        providers = {"p": _FakeProviderConfig(models=models)}
        results = match_all_agents(agents, providers)
        assert len(results) == 1
        # visionary_leader affinity sets priority=quality -> expensive.
        assert results[0].model_id == "expensive"


class TestModelMatcherConfigInjection:
    """Verify operator-tunable matcher weights flow through ``match_model``."""

    @pytest.mark.unit
    def test_default_config_field_defaults(self) -> None:
        config = ModelMatcherConfig()
        assert config.tier_base_score == pytest.approx(0.5)
        assert config.headroom_max_bonus == pytest.approx(0.25)
        assert config.priority_max_bonus == pytest.approx(0.25)
        assert config.headroom_ratio_cap == pytest.approx(2.0)
        assert config.balanced_partial_credit == pytest.approx(0.125)

    @pytest.mark.unit
    def test_custom_tier_base_score_changes_score(self) -> None:
        """Lowering tier_base_score lowers the score floor for tier matches."""
        models = (_make_model("only", cost_input=0.01),)
        requirement = ModelRequirement(tier="medium", priority="quality")
        # Single candidate -> headroom + priority bonuses are full.
        # Default config: 0.5 + 0.25 + 0.25 = 1.0.
        # With tier_base_score=0.1: 0.1 + 0.25 + 0.25 = 0.6.
        config = ModelMatcherConfig(tier_base_score=0.1)
        _, score = match_model(requirement, models, config)
        assert score == pytest.approx(0.6)

    @pytest.mark.unit
    def test_default_arg_uses_default_config(self) -> None:
        """Omitting ``matcher_config`` scores identically to the default."""
        models = (_make_model("only"),)
        requirement = ModelRequirement(tier="medium")
        _, score_default = match_model(requirement, models)
        _, score_explicit = match_model(requirement, models, ModelMatcherConfig())
        assert score_default == pytest.approx(score_explicit)

    @pytest.mark.unit
    def test_from_bridge_config_extracts_matcher_subset(self) -> None:
        """Bridge-config projection wires every matcher field."""
        from synthorg.settings.bridge_configs import EngineBridgeConfig

        bridge = EngineBridgeConfig(
            matcher_tier_base_score=0.6,
            matcher_headroom_max_bonus=0.2,
            matcher_priority_max_bonus=0.2,
            matcher_headroom_ratio_cap=3.0,
            matcher_balanced_partial_credit=0.1,
        )
        config = ModelMatcherConfig.from_bridge_config(bridge)
        assert config.tier_base_score == pytest.approx(0.6)
        assert config.headroom_max_bonus == pytest.approx(0.2)
        assert config.priority_max_bonus == pytest.approx(0.2)
        assert config.headroom_ratio_cap == pytest.approx(3.0)
        assert config.balanced_partial_credit == pytest.approx(0.1)
