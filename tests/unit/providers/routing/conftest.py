"""Test fixtures and factories for the routing subpackage."""

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from synthorg.core.enums import SeniorityLevel
from synthorg.providers.routing.models import (
    ResolvedModel,
    RoutingDecision,
    RoutingRequest,
)
from synthorg.providers.routing.resolver import (
    ModelResolver,
)

# ── Factories ─────────────────────────────────────────────────────


class ResolvedModelFactory(ModelFactory[ResolvedModel]):
    """Factory for ResolvedModel."""

    __model__ = ResolvedModel
    provider_name = "test-provider"
    model_id = "test-medium-001"
    alias = "medium"
    cost_per_1k_input = 0.003
    cost_per_1k_output = 0.015
    max_context = 200_000
    estimated_latency_ms = 500


class RoutingRequestFactory(ModelFactory[RoutingRequest]):
    """Factory for RoutingRequest."""

    __model__ = RoutingRequest
    agent_level = None
    task_type = None
    model_override = None
    remaining_budget = None


class RoutingDecisionFactory(ModelFactory[RoutingDecision]):
    """Factory for RoutingDecision."""

    __model__ = RoutingDecision
    resolved_model = ResolvedModelFactory
    strategy_used = "manual"
    reason = "test decision"
    fallbacks_tried = ()


# ── Standard 3-model provider config ─────────────────────────────

SMALL_MODEL = ProviderModelConfig(
    id="test-small-001",
    alias="small",
    cost_per_1k_input=0.001,
    cost_per_1k_output=0.005,
    max_context=200_000,
    estimated_latency_ms=200,
)

MEDIUM_MODEL = ProviderModelConfig(
    id="test-medium-001",
    alias="medium",
    cost_per_1k_input=0.003,
    cost_per_1k_output=0.015,
    max_context=200_000,
    estimated_latency_ms=500,
)

LARGE_MODEL = ProviderModelConfig(
    id="test-large-001",
    alias="large",
    cost_per_1k_input=0.015,
    cost_per_1k_output=0.075,
    max_context=200_000,
    estimated_latency_ms=1500,
)


@pytest.fixture
def three_model_provider() -> dict[str, ProviderConfig]:
    """Provider config with small, medium, large."""
    return {
        "test-provider": ProviderConfig(
            driver="litellm",
            api_key="sk-test",
            models=(SMALL_MODEL, MEDIUM_MODEL, LARGE_MODEL),
        ),
    }


@pytest.fixture
def resolver(
    three_model_provider: dict[str, ProviderConfig],
) -> ModelResolver:
    """Resolver built from the 3-model provider."""
    return ModelResolver.from_config(three_model_provider)


@pytest.fixture
def standard_routing_config() -> RoutingConfig:
    """Routing config with role-based rules and fallback chain."""
    return RoutingConfig(
        strategy="role_based",
        rules=(
            RoutingRuleConfig(
                role_level=SeniorityLevel.JUNIOR,
                preferred_model="small",
            ),
            RoutingRuleConfig(
                role_level=SeniorityLevel.SENIOR,
                preferred_model="medium",
                fallback="small",
            ),
            RoutingRuleConfig(
                role_level=SeniorityLevel.C_SUITE,
                preferred_model="large",
                fallback="medium",
            ),
            RoutingRuleConfig(
                task_type="review",
                preferred_model="large",
            ),
        ),
        fallback_chain=("medium", "small"),
    )
