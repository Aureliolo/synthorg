"""Test fixtures and factories for the routing subpackage."""

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.config.agent_schema import RoutingConfig, RoutingRuleConfig
from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
)
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
    model_id = "test-capable-001"
    alias = "capable"
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

BASIC_MODEL = ProviderModelConfig(
    id="test-basic-001",
    alias="basic",
    cost_per_1k_input=0.001,
    cost_per_1k_output=0.005,
    max_context=200_000,
    estimated_latency_ms=200,
)

CAPABLE_MODEL = ProviderModelConfig(
    id="test-capable-001",
    alias="capable",
    cost_per_1k_input=0.003,
    cost_per_1k_output=0.015,
    max_context=200_000,
    estimated_latency_ms=500,
)

EXPERT_MODEL = ProviderModelConfig(
    id="test-expert-001",
    alias="expert",
    cost_per_1k_input=0.015,
    cost_per_1k_output=0.075,
    max_context=200_000,
    estimated_latency_ms=1500,
)


def two_provider_config() -> dict[str, ProviderConfig]:
    """Two providers serving the same model ID with different costs."""
    return {
        "test-provider-a": ProviderConfig(
            driver="litellm",
            connection_name="provider-test-a",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.010,
                    cost_per_1k_output=0.050,
                    estimated_latency_ms=1000,
                ),
            ),
        ),
        "test-provider-b": ProviderConfig(
            driver="litellm",
            connection_name="provider-test-b",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.005,
                    estimated_latency_ms=500,
                ),
            ),
        ),
    }


@pytest.fixture
def three_model_provider() -> dict[str, ProviderConfig]:
    """Provider config with one model per capability rung."""
    return {
        "test-provider": ProviderConfig(
            driver="litellm",
            connection_name="provider-test",
            models=(BASIC_MODEL, CAPABLE_MODEL, EXPERT_MODEL),
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
    """Routing config with task-type rules and fallback chain."""
    return RoutingConfig(
        strategy="smart",
        rules=(
            RoutingRuleConfig(
                task_type="triage",
                preferred_model="basic",
            ),
            RoutingRuleConfig(
                task_type="development",
                preferred_model="capable",
                fallback="basic",
            ),
            RoutingRuleConfig(
                task_type="architecture",
                preferred_model="expert",
                fallback="capable",
            ),
            RoutingRuleConfig(
                task_type="review",
                preferred_model="expert",
            ),
        ),
        fallback_chain=("capable", "basic"),
    )
