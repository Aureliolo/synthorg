"""Tests for ModelResolver."""

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.routing.errors import ModelResolutionError
from synthorg.providers.routing.resolver import ModelResolver

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestResolverFromConfig:
    def test_indexes_model_ids(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        resolver = ModelResolver.from_config(three_model_provider)
        model = resolver.resolve("test-medium-001")
        assert model.model_id == "test-medium-001"
        assert model.provider_name == "test-provider"

    def test_indexes_aliases(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        resolver = ModelResolver.from_config(three_model_provider)
        model = resolver.resolve("medium")
        assert model.model_id == "test-medium-001"

    def test_empty_providers(self) -> None:
        resolver = ModelResolver.from_config({})
        assert resolver.all_models() == ()

    def test_multiple_providers(self) -> None:
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-medium-001",
                        alias="medium",
                        cost_per_1k_input=0.003,
                        cost_per_1k_output=0.015,
                    ),
                ),
            ),
            "test-provider-b": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-fast-001",
                        alias="fast",
                        cost_per_1k_input=0.005,
                        cost_per_1k_output=0.015,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        assert len(resolver.all_models()) == 2


class TestResolverResolve:
    def test_resolve_by_id(self, resolver: ModelResolver) -> None:
        model = resolver.resolve("test-small-001")
        assert model.model_id == "test-small-001"

    def test_resolve_by_alias(self, resolver: ModelResolver) -> None:
        model = resolver.resolve("large")
        assert model.model_id == "test-large-001"

    def test_resolve_unknown_raises(self, resolver: ModelResolver) -> None:
        with pytest.raises(ModelResolutionError, match="not found"):
            resolver.resolve("nonexistent")

    def test_resolve_error_contains_context(self, resolver: ModelResolver) -> None:
        with pytest.raises(ModelResolutionError) as exc_info:
            resolver.resolve("nonexistent")
        assert exc_info.value.context["ref"] == "nonexistent"


class TestResolverResolveSafe:
    def test_resolve_safe_found(self, resolver: ModelResolver) -> None:
        model = resolver.resolve_safe("medium")
        assert model is not None
        assert model.model_id == "test-medium-001"

    def test_resolve_safe_not_found(self, resolver: ModelResolver) -> None:
        assert resolver.resolve_safe("nonexistent") is None


class TestResolverAllModels:
    def test_all_models_deduplicates(self, resolver: ModelResolver) -> None:
        models = resolver.all_models()
        ids = [m.model_id for m in models]
        assert len(ids) == len(set(ids))
        assert len(models) == 3

    def test_all_models_sorted_by_cost(self, resolver: ModelResolver) -> None:
        models = resolver.all_models_sorted_by_cost()
        costs = [m.cost_per_1k_input + m.cost_per_1k_output for m in models]
        assert costs == sorted(costs)

    def test_cheapest_is_small(self, resolver: ModelResolver) -> None:
        models = resolver.all_models_sorted_by_cost()
        assert models[0].alias == "small"

    def test_most_expensive_is_large(self, resolver: ModelResolver) -> None:
        models = resolver.all_models_sorted_by_cost()
        assert models[-1].alias == "large"


class TestResolverSortByLatency:
    def test_sorted_ascending(self, resolver: ModelResolver) -> None:
        models = resolver.all_models_sorted_by_latency()
        latencies = [m.estimated_latency_ms for m in models]
        # All have latency: 200, 500, 1500
        assert latencies == [200, 500, 1500]

    def test_none_sorted_last(self) -> None:
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-fast",
                        alias="fast",
                        estimated_latency_ms=100,
                    ),
                    ProviderModelConfig(
                        id="test-no-latency",
                        alias="unknown",
                    ),
                    ProviderModelConfig(
                        id="test-slow",
                        alias="slow",
                        estimated_latency_ms=500,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        models = resolver.all_models_sorted_by_latency()
        assert models[0].model_id == "test-fast"
        assert models[1].model_id == "test-slow"
        assert models[2].model_id == "test-no-latency"
        assert models[2].estimated_latency_ms is None

    def test_all_none_returns_all(self) -> None:
        providers = {
            "test-provider": ProviderConfig(
                models=(
                    ProviderModelConfig(id="test-a", alias="a"),
                    ProviderModelConfig(id="test-b", alias="b"),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        models = resolver.all_models_sorted_by_latency()
        assert len(models) == 2
        assert all(m.estimated_latency_ms is None for m in models)

    def test_wired_from_config(
        self,
        three_model_provider: dict[str, ProviderConfig],
    ) -> None:
        resolver = ModelResolver.from_config(three_model_provider)
        models = resolver.all_models_sorted_by_latency()
        # small=200, medium=500, large=1500
        assert models[0].estimated_latency_ms == 200
        assert models[0].alias == "small"


class TestResolverCollisionDetection:
    def test_duplicate_ref_different_models_raises(self) -> None:
        """Two providers with same alias but different models should raise."""
        providers = {
            "test-provider-a": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-model-a",
                        alias="shared-alias",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                    ),
                ),
            ),
            "test-provider-b": ProviderConfig(
                models=(
                    ProviderModelConfig(
                        id="test-model-b",
                        alias="shared-alias",
                        cost_per_1k_input=0.002,
                        cost_per_1k_output=0.010,
                    ),
                ),
            ),
        }
        with pytest.raises(ModelResolutionError, match="Duplicate model reference"):
            ModelResolver.from_config(providers)


class TestResolverImmutability:
    def test_index_is_immutable(self, resolver: ModelResolver) -> None:
        with pytest.raises(TypeError):
            resolver._index["new"] = None  # type: ignore[index]
