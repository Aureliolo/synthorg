"""Tests for multi-provider model resolution."""

from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.routing.errors import ModelResolutionError
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.providers.routing.router import ModelRouter
from synthorg.providers.routing.selector import (
    CheapestSelector,
    QuotaAwareSelector,
)

from .conftest import two_provider_config as _two_provider_config

pytestmark = pytest.mark.unit

# ── Helpers ──────────────────────────────────────────────────────


def _mixed_provider_config() -> dict[str, ProviderConfig]:
    """Provider A has shared + unique model; provider B has shared only."""
    return {
        "test-provider-a": ProviderConfig(
            driver="litellm",
            connection_name="conn-test-a",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.010,
                    cost_per_1k_output=0.050,
                ),
                ProviderModelConfig(
                    id="test-unique-001",
                    alias="unique",
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.015,
                ),
            ),
        ),
        "test-provider-b": ProviderConfig(
            driver="litellm",
            connection_name="conn-test-b",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.005,
                ),
            ),
        ),
    }


# ── TestMultiProviderIndex ───────────────────────────────────────


class TestMultiProviderIndex:
    def test_same_model_id_different_providers_no_error(self) -> None:
        """Two providers registering the same model_id should not raise."""
        resolver = ModelResolver.from_config(_two_provider_config())
        model = resolver.resolve("test-shared-001")
        assert model.model_id == "test-shared-001"

    def test_same_alias_different_providers_no_error(self) -> None:
        """Two providers registering the same alias should not raise."""
        resolver = ModelResolver.from_config(_two_provider_config())
        model = resolver.resolve("shared")
        assert model.model_id == "test-shared-001"

    def test_resolve_all_returns_all_candidates(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        candidates = resolver.resolve_all("shared")
        assert len(candidates) == 2
        providers = {c.provider_name for c in candidates}
        assert providers == {"test-provider-a", "test-provider-b"}

    def test_resolve_all_by_model_id(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        candidates = resolver.resolve_all("test-shared-001")
        assert len(candidates) == 2

    def test_resolve_all_empty_for_unknown(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        assert resolver.resolve_all("nonexistent") == ()

    def test_all_models_returns_all_variants(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        models = resolver.all_models()
        assert len(models) == 2
        providers = {m.provider_name for m in models}
        assert providers == {"test-provider-a", "test-provider-b"}

    def test_all_models_mixed_shared_and_unique(self) -> None:
        resolver = ModelResolver.from_config(_mixed_provider_config())
        models = resolver.all_models()
        # 2 shared variants + 1 unique = 3
        assert len(models) == 3

    def test_all_models_sorted_by_cost_includes_all_variants(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        models = resolver.all_models_sorted_by_cost()
        assert len(models) == 2
        # Cheapest first
        assert models[0].provider_name == "test-provider-b"
        assert models[1].provider_name == "test-provider-a"

    def test_all_models_sorted_by_latency_includes_all_variants(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        models = resolver.all_models_sorted_by_latency()
        assert len(models) == 2
        # Fastest first (500ms < 1000ms)
        assert models[0].provider_name == "test-provider-b"

    def test_three_providers_same_model(self) -> None:
        """Three providers for the same model all register correctly."""
        providers = {
            "test-provider-a": ProviderConfig(
                driver="litellm",
                connection_name="conn-a",
                models=(
                    ProviderModelConfig(
                        id="test-shared-001",
                        alias="shared",
                        cost_per_1k_input=0.010,
                        cost_per_1k_output=0.050,
                    ),
                ),
            ),
            "test-provider-b": ProviderConfig(
                driver="litellm",
                connection_name="conn-b",
                models=(
                    ProviderModelConfig(
                        id="test-shared-001",
                        alias="shared",
                        cost_per_1k_input=0.001,
                        cost_per_1k_output=0.005,
                    ),
                ),
            ),
            "test-provider-c": ProviderConfig(
                driver="litellm",
                connection_name="conn-c",
                models=(
                    ProviderModelConfig(
                        id="test-shared-001",
                        alias="shared",
                        cost_per_1k_input=0.005,
                        cost_per_1k_output=0.025,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        candidates = resolver.resolve_all("shared")
        assert len(candidates) == 3
        providers_seen = {c.provider_name for c in candidates}
        assert providers_seen == {
            "test-provider-a",
            "test-provider-b",
            "test-provider-c",
        }
        # Default selector picks cheapest (provider-b)
        model = resolver.resolve("shared")
        assert model.provider_name == "test-provider-b"

    def test_exact_duplicate_skipped(self) -> None:
        """Same provider, same model, same ref should not create duplicates."""
        providers = {
            "test-provider": ProviderConfig(
                driver="litellm",
                connection_name="conn-test",
                models=(
                    ProviderModelConfig(
                        id="test-model-001",
                        alias="model",
                        cost_per_1k_input=0.003,
                        cost_per_1k_output=0.015,
                    ),
                ),
            ),
        }
        resolver = ModelResolver.from_config(providers)
        # model_id and alias both point to same model -- only one candidate
        candidates_by_id = resolver.resolve_all("test-model-001")
        candidates_by_alias = resolver.resolve_all("model")
        assert len(candidates_by_id) == 1
        assert len(candidates_by_alias) == 1


# ── TestMultiProviderWithSelector ────────────────────────────────


class TestMultiProviderWithSelector:
    def test_default_selector_is_quota_aware(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        assert isinstance(resolver.selector, QuotaAwareSelector)

    def test_resolve_uses_injected_selector(self) -> None:
        mock_selector = MagicMock()
        expected = ResolvedModel(
            provider_name="test-provider-a",
            model_id="test-shared-001",
            cost_per_1k_input=0.010,
            cost_per_1k_output=0.050,
        )
        mock_selector.select.return_value = expected

        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        result = resolver.resolve("shared")

        mock_selector.select.assert_called_once()
        candidates = mock_selector.select.call_args[0][0]
        assert len(candidates) == 2
        assert {c.provider_name for c in candidates} == {
            "test-provider-a",
            "test-provider-b",
        }
        assert result is expected

    def test_resolve_safe_uses_selector(self) -> None:
        mock_selector = MagicMock()
        expected = ResolvedModel(
            provider_name="test-provider-b",
            model_id="test-shared-001",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.005,
        )
        mock_selector.select.return_value = expected

        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        result = resolver.resolve_safe("shared")

        mock_selector.select.assert_called_once()
        candidates = mock_selector.select.call_args[0][0]
        assert len(candidates) == 2
        assert result is expected

    def test_cheapest_selector_picks_cheapest(self) -> None:
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=CheapestSelector(),
        )
        model = resolver.resolve("shared")
        assert model.provider_name == "test-provider-b"

    def test_quota_aware_prefers_available(self) -> None:
        selector = QuotaAwareSelector(
            provider_quota_available={
                "test-provider-a": True,
                "test-provider-b": False,
            },
        )
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=selector,
        )
        model = resolver.resolve("shared")
        assert model.provider_name == "test-provider-a"

    def test_from_config_passes_selector(self) -> None:
        selector = CheapestSelector()
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=selector,
        )
        assert resolver.selector is selector


# ── TestMultiProviderImmutability ────────────────────────────────


class TestMultiProviderImmutability:
    def test_index_is_mapping_proxy(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        assert isinstance(resolver._index, MappingProxyType)

    def test_candidate_tuples_are_immutable(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        candidates = resolver.resolve_all("shared")
        assert isinstance(candidates, tuple)

    def test_index_mutation_blocked(self) -> None:
        resolver = ModelResolver.from_config(_two_provider_config())
        with pytest.raises(TypeError):
            resolver._index["new"] = ()  # type: ignore[index]

    def test_empty_tuple_in_index_rejected(self) -> None:
        with pytest.raises(ValueError, match="Empty candidate list"):
            ModelResolver({"bad-ref": ()})


# ── TestSelectorErrorWrapping ────────────────────────────────────


class TestSelectorErrorWrapping:
    """Tests for exception handling when selectors fail."""

    def test_resolve_wraps_arbitrary_selector_exception(self) -> None:
        """resolve() wraps unexpected selector errors as ModelResolutionError."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = RuntimeError("boom")
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        with pytest.raises(ModelResolutionError, match="boom"):
            resolver.resolve("shared")

    def test_resolve_safe_returns_none_on_selector_exception(self) -> None:
        """resolve_safe() returns None when selector raises unexpectedly."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = RuntimeError("boom")
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        assert resolver.resolve_safe("shared") is None

    def test_resolve_propagates_memory_error(self) -> None:
        """MemoryError from selector propagates unchanged."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = MemoryError
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        with pytest.raises(MemoryError):
            resolver.resolve("shared")

    def test_resolve_safe_propagates_memory_error(self) -> None:
        """MemoryError from selector propagates even in resolve_safe."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = MemoryError
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        with pytest.raises(MemoryError):
            resolver.resolve_safe("shared")

    def test_resolve_reraises_model_resolution_error(self) -> None:
        """ModelResolutionError from selector re-raises directly."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = ModelResolutionError(
            "empty",
            context={"selector": "test"},
        )
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        with pytest.raises(ModelResolutionError, match="empty"):
            resolver.resolve("shared")

    def test_resolve_safe_returns_none_on_resolution_error(self) -> None:
        """resolve_safe() returns None on ModelResolutionError from selector."""
        mock_selector = MagicMock()
        mock_selector.select.side_effect = ModelResolutionError(
            "empty",
            context={"selector": "test"},
        )
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=mock_selector,
        )
        assert resolver.resolve_safe("shared") is None


# ── TestRouterSelectorPassthrough ────────────────────────────────


class TestRouterSelectorPassthrough:
    """Tests that ModelRouter correctly passes selector to ModelResolver."""

    def test_router_passes_selector_to_resolver(self) -> None:
        """Selector injected into ModelRouter reaches the resolver."""
        from synthorg.config.agent_schema import RoutingConfig

        selector = CheapestSelector()
        router = ModelRouter(
            routing_config=RoutingConfig(),
            providers=_two_provider_config(),
            selector=selector,
        )
        assert router._resolver.selector is selector
