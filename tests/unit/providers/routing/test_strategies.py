"""Tests for routing strategies."""

import pytest

from ai_company.config.schema import (
    ProviderConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from ai_company.core.enums import SeniorityLevel
from ai_company.providers.routing.errors import (
    ModelResolutionError,
    NoAvailableModelError,
)
from ai_company.providers.routing.models import RoutingRequest
from ai_company.providers.routing.resolver import ModelResolver
from ai_company.providers.routing.strategies import (
    STRATEGY_MAP,
    CostAwareStrategy,
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
        [ManualStrategy, RoleBasedStrategy, CostAwareStrategy, SmartStrategy],
    )
    def test_implements_protocol(self, cls: type) -> None:
        assert isinstance(cls(), RoutingStrategy)

    def test_strategy_map_has_all_names(self) -> None:
        expected = {"manual", "role_based", "cost_aware", "smart", "cheapest"}
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

        assert decision.resolved_model.model_id == "test-sonnet-001"
        assert decision.strategy_used == "manual"
        assert "override" in decision.reason.lower()

    def test_resolves_by_model_id(
        self,
        resolver: ModelResolver,
    ) -> None:
        strategy = ManualStrategy()
        request = RoutingRequest(model_override="test-opus-001")

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.model_id == "test-opus-001"

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
        """MID has no rule -> uses seniority catalog (sonnet tier)."""
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
        """LEAD has tier=opus; if opus not registered, use fallback chain."""
        provider = ProviderConfig(
            models=(
                three_model_provider["test-provider"].models[0],  # haiku only
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
                three_model_provider["test-provider"].models[0],  # haiku only
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

        # review rule -> opus; junior role rule -> haiku; task wins
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
        # Build resolver with only haiku
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
                three_model_provider["test-provider"].models[0],  # haiku only
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
            models=(three_model_provider["test-provider"].models[0],),  # haiku only
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
            models=(three_model_provider["test-provider"].models[0],),  # haiku only
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
            models=(three_model_provider["test-provider"].models[0],),  # haiku only
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
        """Budget large enough for haiku+sonnet but not opus picks haiku."""
        # haiku total=0.006, sonnet total=0.018, opus total=0.090
        strategy = CostAwareStrategy()
        request = RoutingRequest(remaining_budget=0.02)

        decision = strategy.select(request, RoutingConfig(), resolver)

        assert decision.resolved_model.alias == "small"
        assert "exceed" not in decision.reason.lower()
