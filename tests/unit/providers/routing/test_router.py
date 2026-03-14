"""Tests for ModelRouter."""

import pytest
import structlog

from synthorg.config.schema import ProviderConfig, RoutingConfig
from synthorg.core.enums import SeniorityLevel
from synthorg.observability.events.routing import (
    ROUTING_DECISION_MADE,
    ROUTING_ROUTER_BUILT,
)
from synthorg.providers.routing.errors import (
    ModelResolutionError,
    NoAvailableModelError,
    UnknownStrategyError,
)
from synthorg.providers.routing.models import RoutingRequest
from synthorg.providers.routing.router import ModelRouter

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestModelRouterConstruction:
    def test_builds_with_valid_config(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cost_aware")
        router = ModelRouter(config, three_model_provider)
        assert router.strategy_name == "cost_aware"

    def test_builds_with_cheapest_alias(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cheapest")
        router = ModelRouter(config, three_model_provider)
        assert router.strategy_name == "cost_aware"

    def test_raises_for_unknown_strategy(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="nonexistent")
        with pytest.raises(UnknownStrategyError, match="nonexistent"):
            ModelRouter(config, three_model_provider)

    def test_resolver_accessible(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cost_aware")
        router = ModelRouter(config, three_model_provider)
        assert len(router.resolver.all_models()) == 3

    def test_logs_router_built(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cost_aware")
        with structlog.testing.capture_logs() as cap:
            ModelRouter(config, three_model_provider)
        events = [e for e in cap if e.get("event") == ROUTING_ROUTER_BUILT]
        assert len(events) == 1
        assert events[0]["strategy"] == "cost_aware"


class TestModelRouterRoute:
    def test_routes_cost_aware(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cost_aware")
        router = ModelRouter(config, three_model_provider)

        decision = router.route(RoutingRequest())

        assert decision.resolved_model.alias == "small"
        assert decision.strategy_used == "cost_aware"

    def test_routes_manual(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="manual")
        router = ModelRouter(config, three_model_provider)

        decision = router.route(
            RoutingRequest(model_override="large"),
        )

        assert decision.resolved_model.model_id == "test-large-001"

    def test_routes_role_based(
        self,
        three_model_provider: dict[str, ProviderConfig],
        standard_routing_config: RoutingConfig,
    ) -> None:
        router = ModelRouter(standard_routing_config, three_model_provider)

        decision = router.route(
            RoutingRequest(agent_level=SeniorityLevel.SENIOR),
        )

        assert decision.resolved_model.alias == "medium"

    def test_routes_smart(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="smart")
        router = ModelRouter(config, three_model_provider)

        decision = router.route(
            RoutingRequest(agent_level=SeniorityLevel.C_SUITE),
        )

        assert decision.resolved_model.alias == "large"

    def test_routes_fastest(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="fastest")
        router = ModelRouter(config, three_model_provider)

        decision = router.route(RoutingRequest())

        # small has lowest latency (200ms)
        assert decision.resolved_model.alias == "small"
        assert decision.strategy_used == "fastest"

    def test_logs_decision(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="cost_aware")
        router = ModelRouter(config, three_model_provider)

        with structlog.testing.capture_logs() as cap:
            router.route(RoutingRequest())

        events = [e for e in cap if e.get("event") == ROUTING_DECISION_MADE]
        assert len(events) == 1
        assert events[0]["strategy"] == "cost_aware"

    def test_manual_no_override_raises(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        config = RoutingConfig(strategy="manual")
        router = ModelRouter(config, three_model_provider)

        with pytest.raises(ModelResolutionError):
            router.route(RoutingRequest())

    def test_empty_providers_cost_aware_raises(self) -> None:
        config = RoutingConfig(strategy="cost_aware")
        router = ModelRouter(config, {})

        with pytest.raises(NoAvailableModelError):
            router.route(RoutingRequest())

    def test_logs_warning_on_routing_failure(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        """When route() raises, a warning log should be emitted."""
        from synthorg.observability.events.routing import ROUTING_SELECTION_FAILED

        config = RoutingConfig(strategy="manual")
        router = ModelRouter(config, three_model_provider)

        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ModelResolutionError),
        ):
            router.route(RoutingRequest())

        warnings = [
            e
            for e in cap
            if e.get("event") == ROUTING_SELECTION_FAILED
            and e.get("log_level") == "warning"
        ]
        assert len(warnings) == 1
        assert warnings[0]["strategy"] == "manual"
