"""Tests for routing strategies."""

import pytest

from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from synthorg.core.enums import SeniorityLevel
from synthorg.providers.routing.errors import (
    ModelResolutionError,
    NoAvailableModelError,
)
from synthorg.providers.routing.models import RoutingRequest
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.providers.routing.strategies import (
    STRATEGY_MAP,
    CostAwareStrategy,
    FastestStrategy,
    ManualStrategy,
    RoleBasedStrategy,
    RoutingStrategy,
    SmartStrategy,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


# ── Protocol conformance ─────────────────────────────────────────


class TestRoutingStrategyProtocol:
    @pytest.mark.parametrize(
        "cls",
        [
            ManualStrategy,
            RoleBasedStrategy,
            CostAwareStrategy,
            FastestStrategy,
            SmartStrategy,
        ],
    )
    def test_implements_protocol(self, cls: type) -> None:
        assert isinstance(cls(), RoutingStrategy)

    def test_strategy_map_has_all_names(self) -> None:
        expected = {
            "manual",
            "role_based",
            "cost_aware",
            "fastest",
            "smart",
            "cheapest",
        }
        assert set(STRATEGY_MAP) == expected


# ── ManualStrategy ───────────────────────────────────────────────


class TestManualStrategy:
    def test_resolves_explicit_override(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = ManualStrategy()
        request = RoutingRequest(model_override="medium")
        config = RoutingConfig()

        decision = strategy.select(request, config, resolver)

        assert decision.resolved_model.model_id == "test-medium-001"
        assert decision.strategy_used == "manual"
        assert "override" in decision.reason.lower()

    def test_resolves_by_model_id(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = ManualStrategy()
        request = RoutingRequest(model_override="test-large-001")

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.model_id == "test-large-001"

    def test_raises_without_override(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = ManualStrategy()
        request = RoutingRequest()

        with pytest.raises(ModelResolutionError, match="model_override"):
            strategy.select(request, RoutingConfig(), resolver)

    def test_raises_for_unknown_model(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = ManualStrategy()
        request = RoutingRequest(model_override="nonexistent")

        with pytest.raises(ModelResolutionError, match="not found"):
            strategy.select(request, RoutingConfig(), resolver)


# ── RoleBasedStrategy ────────────────────────────────────────────


class TestRoleBasedStrategy:
    def test_matches_role_rule(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = RoleBasedStrategy()
        request = RoutingRequest(agent_level=SeniorityLevel.JUNIOR)

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "small"
        assert decision.strategy_used == "role_based"

    def test_matches_senior_rule(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = RoleBasedStrategy()
        request = RoutingRequest(agent_level=SeniorityLevel.SENIOR)

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "medium"

    def test_matches_csuite_rule(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = RoleBasedStrategy()
        request = RoutingRequest(agent_level=SeniorityLevel.C_SUITE)

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "large"

    def test_falls_back_to_seniority_default(
        self,
        resolver: ModelResolver,
    ) -> None:
        """MID has no rule -> uses seniority catalog (medium tier)."""
        strategy = RoleBasedStrategy()
        config = RoutingConfig(strategy="role_based")
        request = RoutingRequest(agent_level=SeniorityLevel.MID)

        decision = strategy.select(request, config, resolver)

        assert decision.resolved_model.alias == "medium"
        assert "seniority" in decision.reason.lower()

    def test_falls_back_to_global_chain(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """LEAD has tier=large; if large not registered, use fallback chain."""
        provider = ProviderConfig(
            models=(
                three_model_provider["test-provider"].models[0],  # small only
            ),
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="role_based",
            fallback_chain=("small",),
        )
        request = RoutingRequest(agent_level=SeniorityLevel.LEAD)

        decision = RoleBasedStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"

    def test_raises_without_agent_level(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = RoleBasedStrategy()
        request = RoutingRequest()

        with pytest.raises(ModelResolutionError, match="agent_level"):
            strategy.select(request, RoutingConfig(), resolver)

    def test_raises_when_no_models_available(self) -> None:
        resolver = ModelResolver.from_config({})
        config = RoutingConfig(strategy="role_based")
        request = RoutingRequest(agent_level=SeniorityLevel.MID)

        with pytest.raises(NoAvailableModelError):
            RoleBasedStrategy().select(request, config, resolver)

    def test_rule_fallback_used(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """When preferred not found, rule's fallback is tried."""
        provider = ProviderConfig(
            models=(
                three_model_provider["test-provider"].models[0],  # small only
            ),
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="role_based",
            rules=(
                RoutingRuleConfig(
                    role_level=SeniorityLevel.SENIOR,
                    preferred_model="medium",  # not available
                    fallback="small",
                ),
            ),
        )
        request = RoutingRequest(agent_level=SeniorityLevel.SENIOR)

        decision = RoleBasedStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"
        assert "medium" in decision.fallbacks_tried


# ── CostAwareStrategy ────────────────────────────────────────────


class TestCostAwareStrategy:
    def test_picks_cheapest(self, resolver: ModelResolver) -> None:
        strategy = CostAwareStrategy()
        request = RoutingRequest()

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert decision.strategy_used == "cost_aware"

    def test_task_type_rule_takes_priority(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = CostAwareStrategy()
        request = RoutingRequest(task_type="review")

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "large"

    def test_tight_budget_picks_cheapest(self, resolver: ModelResolver) -> None:
        """With tight budget, should still return cheapest."""
        strategy = CostAwareStrategy()
        request = RoutingRequest(remaining_budget=0.01)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"

    def test_budget_exceeded_still_returns(self, resolver: ModelResolver) -> None:
        """Even if budget is 0.0, returns cheapest with warning."""
        strategy = CostAwareStrategy()
        request = RoutingRequest(remaining_budget=0.0)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" in decision.reason.lower()

    def test_no_models_raises(self) -> None:
        resolver = ModelResolver.from_config({})
        strategy = CostAwareStrategy()

        with pytest.raises(NoAvailableModelError):
            strategy.select(
                RoutingRequest(),
                RoutingConfig(),
                resolver,
            )

    def test_task_type_rule_skipped_when_over_budget(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        """Task-type rule picks 'large' but budget is too low -> cheapest."""
        strategy = CostAwareStrategy()
        # review rule -> large (total_cost=0.090), budget below that
        request = RoutingRequest(task_type="review", remaining_budget=0.02)

        decision = strategy.select(request, standard_routing_config, resolver)

        # Should fall through to cheapest, not use the over-budget large model
        assert decision.resolved_model.alias == "small"

    def test_task_type_miss_falls_to_cheapest(
        self,
        resolver: ModelResolver,
    ) -> None:
        """Unmatched task_type => cheapest."""
        strategy = CostAwareStrategy()
        config = RoutingConfig(
            rules=(
                RoutingRuleConfig(
                    task_type="review",
                    preferred_model="large",
                ),
            ),
        )
        request = RoutingRequest(task_type="development")

        decision = strategy.select(request, config, resolver)

        assert decision.resolved_model.alias == "small"


# ── FastestStrategy ──────────────────────────────────────────────


class TestFastestStrategy:
    def test_picks_fastest(self, resolver: ModelResolver) -> None:
        strategy = FastestStrategy()
        request = RoutingRequest()

        decision = strategy.select(request, RoutingConfig(), resolver)

        # small has lowest latency (200ms)
        assert decision.resolved_model.alias == "small"
        assert decision.strategy_used == "fastest"

    def test_task_type_rule_takes_priority(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = FastestStrategy()
        request = RoutingRequest(task_type="review")

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "large"

    def test_budget_respected(self, resolver: ModelResolver) -> None:
        """With a budget, should pick fastest within budget."""
        strategy = FastestStrategy()
        # small total=0.006, medium total=0.018, large total=0.090
        request = RoutingRequest(remaining_budget=0.02)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" not in decision.reason.lower()

    def test_budget_exceeded_still_returns(self, resolver: ModelResolver) -> None:
        """Even if budget is 0.0, returns fastest with warning."""
        strategy = FastestStrategy()
        request = RoutingRequest(remaining_budget=0.0)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" in decision.reason.lower()

    def test_no_models_raises(self) -> None:
        resolver = ModelResolver.from_config({})
        strategy = FastestStrategy()

        with pytest.raises(NoAvailableModelError):
            strategy.select(
                RoutingRequest(),
                RoutingConfig(),
                resolver,
            )

    def test_no_latency_data_falls_back_to_cheapest(self) -> None:
        """When no models have latency data, delegates to cheapest."""
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-expensive",
                        alias="expensive",
                        cost_per_1k_input=0.010,
                        cost_per_1k_output=0.050,
                    ),
                    ProviderModelConfig(
                        id="test-cheap",
                        alias="cheap",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        strategy = FastestStrategy()

        decision = strategy.select(
            RoutingRequest(),
            RoutingConfig(),
            resolver,
        )

        assert decision.resolved_model.alias == "cheap"

    def test_task_type_rule_skipped_when_over_budget(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        """Task-type rule picks 'large' but budget is too low -> fastest."""
        strategy = FastestStrategy()
        # review rule -> large (total_cost=0.090), budget below that
        request = RoutingRequest(task_type="review", remaining_budget=0.02)

        decision = strategy.select(request, standard_routing_config, resolver)

        # Should fall through to fastest within budget, not the over-budget model
        assert decision.resolved_model.alias == "small"
        assert "task-type rule" in decision.reason.lower()

    def test_budget_exceeded_with_latency_models_only(self) -> None:
        """All models with latency exceed budget -> fastest with warning."""
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-fast-expensive",
                        alias="fast-expensive",
                        cost_per_1k_input=0.010,
                        cost_per_1k_output=0.050,
                        estimated_latency_ms=100,
                    ),
                    ProviderModelConfig(
                        id="test-slow-expensive",
                        alias="slow-expensive",
                        cost_per_1k_input=0.015,
                        cost_per_1k_output=0.075,
                        estimated_latency_ms=500,
                    ),
                    ProviderModelConfig(
                        id="test-no-latency-cheap",
                        alias="no-latency-cheap",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        strategy = FastestStrategy()

        decision = strategy.select(
            RoutingRequest(remaining_budget=0.001),
            RoutingConfig(),
            resolver,
        )

        # Returns fastest with latency data, not the cheap no-latency one
        assert decision.resolved_model.alias == "fast-expensive"
        assert "exceed" in decision.reason.lower()

    def test_mixed_none_non_none_ignores_none(self) -> None:
        """Models with None latency are ignored when others have data."""
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-slow-cheap",
                        alias="slow-cheap",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                        estimated_latency_ms=800,
                    ),
                    ProviderModelConfig(
                        id="test-no-latency",
                        alias="no-latency",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                    ),
                    ProviderModelConfig(
                        id="test-fast-expensive",
                        alias="fast-expensive",
                        cost_per_1k_input=0.010,
                        cost_per_1k_output=0.050,
                        estimated_latency_ms=100,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        strategy = FastestStrategy()

        decision = strategy.select(
            RoutingRequest(),
            RoutingConfig(),
            resolver,
        )

        # Should pick fast-expensive (100ms), not no-latency
        assert decision.resolved_model.alias == "fast-expensive"

    def test_budget_picks_slower_when_fastest_exceeds(self) -> None:
        """Fastest model exceeds budget, slower model is within budget."""
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-fast-expensive",
                        alias="fast-expensive",
                        cost_per_1k_input=0.050,
                        cost_per_1k_output=0.100,
                        estimated_latency_ms=100,
                    ),
                    ProviderModelConfig(
                        id="test-medium-speed",
                        alias="medium-speed",
                        cost_per_1k_input=0.005,
                        cost_per_1k_output=0.010,
                        estimated_latency_ms=500,
                    ),
                    ProviderModelConfig(
                        id="test-slow-cheap",
                        alias="slow-cheap",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                        estimated_latency_ms=1000,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        strategy = FastestStrategy()

        # Budget 0.02: fast-expensive total=0.150 (exceeds),
        # medium-speed total=0.015 (within budget)
        decision = strategy.select(
            RoutingRequest(remaining_budget=0.02),
            RoutingConfig(),
            resolver,
        )

        assert decision.resolved_model.alias == "medium-speed"
        assert "exceed" not in decision.reason.lower()


# ── SmartStrategy ────────────────────────────────────────────────


class TestSmartStrategy:
    def test_override_takes_priority(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = SmartStrategy()
        request = RoutingRequest(
            model_override="large",
            agent_level=SeniorityLevel.JUNIOR,
            task_type="review",
        )

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "large"
        assert "override" in decision.reason.lower()

    def test_task_type_before_role(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = SmartStrategy()
        request = RoutingRequest(
            agent_level=SeniorityLevel.JUNIOR,
            task_type="review",
        )

        decision = strategy.select(request, standard_routing_config, resolver)

        # review rule -> large; junior role rule -> small; task wins
        assert decision.resolved_model.alias == "large"
        assert "task-type" in decision.reason.lower()

    def test_role_rule_when_no_task_match(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        strategy = SmartStrategy()
        request = RoutingRequest(agent_level=SeniorityLevel.JUNIOR)

        decision = strategy.select(request, standard_routing_config, resolver)

        assert decision.resolved_model.alias == "small"

    def test_seniority_default_when_no_rules(
        self,
        resolver: ModelResolver,
    ) -> None:
        """No rules -> uses seniority catalog."""
        strategy = SmartStrategy()
        config = RoutingConfig()
        request = RoutingRequest(agent_level=SeniorityLevel.MID)

        decision = strategy.select(request, config, resolver)

        assert decision.resolved_model.alias == "medium"
        assert "seniority" in decision.reason.lower()

    def test_cheapest_when_no_level(self, resolver: ModelResolver) -> None:
        strategy = SmartStrategy()
        request = RoutingRequest()

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"

    def test_fallback_chain_last_resort(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """Empty resolver but fallback chain has a valid ref."""
        # Build resolver with only small
        provider = ProviderConfig(
            models=(three_model_provider["test-provider"].models[0],),
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(fallback_chain=("small",))
        # Override is unknown, no role, no task
        request = RoutingRequest(model_override="nonexistent")

        decision = SmartStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"

    def test_raises_when_nothing_available(self) -> None:
        resolver = ModelResolver.from_config({})
        config = RoutingConfig()
        request = RoutingRequest()

        with pytest.raises(NoAvailableModelError):
            SmartStrategy().select(request, config, resolver)

    def test_budget_aware_in_cheapest_fallback(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = SmartStrategy()
        request = RoutingRequest(remaining_budget=0.0)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" in decision.reason.lower()

    def test_override_soft_fail_falls_through(
        self,
        resolver: ModelResolver,
        standard_routing_config: RoutingConfig,
    ) -> None:
        """Unresolvable override in SmartStrategy falls through (not raise)."""
        strategy = SmartStrategy()
        request = RoutingRequest(
            model_override="nonexistent",
            agent_level=SeniorityLevel.JUNIOR,
        )

        decision = strategy.select(
            request,
            standard_routing_config,
            resolver,
        )

        # Should NOT have used the override signal
        assert "override" not in decision.reason.lower()
        # Should have fallen through to a role rule or seniority default
        assert decision.resolved_model is not None

    def test_full_three_stage_fallback(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """Primary miss -> rule fallback miss -> global chain hit."""
        provider = ProviderConfig(
            models=(
                three_model_provider["test-provider"].models[0],  # small only
            ),
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="role_based",
            rules=(
                RoutingRuleConfig(
                    role_level=SeniorityLevel.SENIOR,
                    preferred_model="nonexistent",
                    fallback="also-nonexistent",
                ),
            ),
            fallback_chain=("small",),
        )
        request = RoutingRequest(agent_level=SeniorityLevel.SENIOR)

        decision = RoleBasedStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"
        assert "nonexistent" in decision.fallbacks_tried
        assert "also-nonexistent" in decision.fallbacks_tried


class TestGlobalFallbackChain:
    def test_skips_unresolvable_entries(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """Global chain should skip unknown refs and resolve the first valid."""
        provider = ProviderConfig(
            models=(three_model_provider["test-provider"].models[0],),  # small only
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="smart",
            fallback_chain=("nonexistent-a", "nonexistent-b", "small"),
        )
        request = RoutingRequest()

        decision = SmartStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"

    def test_role_based_exhausted_non_empty_chain(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """RoleBasedStrategy raises when all fallback_chain refs are invalid."""
        provider = ProviderConfig(
            models=(three_model_provider["test-provider"].models[0],),  # small only
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="role_based",
            fallback_chain=("nonexistent-x", "nonexistent-y"),
        )
        request = RoutingRequest(agent_level=SeniorityLevel.C_SUITE)

        with pytest.raises(NoAvailableModelError):
            RoleBasedStrategy().select(request, config, resolver)


class TestRuleFallbackDedup:
    def test_dedup_when_rule_fallback_equals_primary(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """When rule fallback equals preferred, it should not retry."""
        provider = ProviderConfig(
            models=(three_model_provider["test-provider"].models[0],),  # small only
        )
        resolver = ModelResolver.from_config({"test-provider": provider})
        config = RoutingConfig(
            strategy="role_based",
            rules=(
                RoutingRuleConfig(
                    role_level=SeniorityLevel.SENIOR,
                    preferred_model="nonexistent",
                    fallback="nonexistent",  # same as preferred
                ),
            ),
            fallback_chain=("small",),
        )
        request = RoutingRequest(agent_level=SeniorityLevel.SENIOR)

        decision = RoleBasedStrategy().select(request, config, resolver)

        assert decision.resolved_model.alias == "small"
        # "nonexistent" should appear only once in tried (deduped)
        assert decision.fallbacks_tried.count("nonexistent") == 1


class TestCostAwareMidRangeBudget:
    def test_mid_range_budget_picks_cheapest_within(
        self,
        resolver: ModelResolver,
    ) -> None:
        """Budget large enough for small+medium but not large picks small."""
        # small total=0.006, medium total=0.018, large total=0.090
        strategy = CostAwareStrategy()
        request = RoutingRequest(remaining_budget=0.02)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" not in decision.reason.lower()
