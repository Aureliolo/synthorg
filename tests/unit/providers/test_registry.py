"""Unit tests for ProviderRegistry."""

from typing import TYPE_CHECKING

import pytest
import structlog

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.observability.events.provider import (
    PROVIDER_DRIVER_NOT_REGISTERED,
    PROVIDER_REGISTRY_BUILT,
)
from synthorg.providers.base import BaseCompletionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from synthorg.providers.capabilities import ModelCapabilities
    from synthorg.providers.models import (
        ChatMessage,
        CompletionConfig,
        CompletionResponse,
        StreamChunk,
        ToolDefinition,
    )
from synthorg.providers.errors import (
    DriverFactoryNotFoundError,
    DriverNotRegisteredError,
)
from synthorg.providers.registry import ProviderRegistry

# ── Helpers ──────────────────────────────────────────────────────


def _make_config(
    *,
    driver: str = "litellm",
    api_key: str | None = "sk-test",
) -> ProviderConfig:
    return ProviderConfig(
        driver=driver,
        api_key=api_key,
        models=(
            ProviderModelConfig(
                id="test-model",
                alias="test",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ),
    )


class _StubDriver(BaseCompletionProvider):
    """Minimal concrete driver for registry tests."""

    def __init__(self, provider_name: str, config: ProviderConfig) -> None:
        self.provider_name = provider_name
        self.config = config

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        raise NotImplementedError

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        raise NotImplementedError


# ── get() ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRegistryGet:
    def test_get_returns_registered_driver(self) -> None:
        driver: BaseCompletionProvider = _StubDriver("example-provider", _make_config())
        registry = ProviderRegistry({"example-provider": driver})

        result = registry.get("example-provider")

        assert result is driver

    def test_get_raises_for_unknown_name(self) -> None:
        registry = ProviderRegistry({})

        with pytest.raises(DriverNotRegisteredError, match="not registered"):
            registry.get("nonexistent")

    def test_get_error_lists_available_providers(self) -> None:
        driver: BaseCompletionProvider = _StubDriver("example-provider", _make_config())
        registry = ProviderRegistry({"example-provider": driver})

        with pytest.raises(DriverNotRegisteredError, match="example-provider"):
            registry.get("other-provider")


# ── list_providers() ─────────────────────────────────────────────


@pytest.mark.unit
class TestRegistryListProviders:
    def test_list_providers_returns_sorted_names(self) -> None:
        drivers: dict[str, BaseCompletionProvider] = {
            "provider-c": _StubDriver("provider-c", _make_config()),
            "example-provider": _StubDriver("example-provider", _make_config()),
            "provider-b": _StubDriver("provider-b", _make_config()),
        }
        registry = ProviderRegistry(drivers)

        result = registry.list_providers()

        assert result == ("example-provider", "provider-b", "provider-c")

    def test_list_providers_empty_registry(self) -> None:
        registry = ProviderRegistry({})

        assert registry.list_providers() == ()


# ── __contains__ / __len__ ───────────────────────────────────────


@pytest.mark.unit
class TestRegistryContainsAndLen:
    def test_contains_registered_provider(self) -> None:
        driver: BaseCompletionProvider = _StubDriver("example-provider", _make_config())
        registry = ProviderRegistry({"example-provider": driver})

        assert "example-provider" in registry
        assert "unknown" not in registry

    def test_contains_unhashable_returns_false(self) -> None:
        registry = ProviderRegistry({})
        assert [1, 2, 3] not in registry

    def test_len_reflects_registered_count(self) -> None:
        drivers: dict[str, BaseCompletionProvider] = {
            "a": _StubDriver("a", _make_config()),
            "b": _StubDriver("b", _make_config()),
        }
        registry = ProviderRegistry(drivers)

        assert len(registry) == 2

    def test_empty_registry_len_zero(self) -> None:
        assert len(ProviderRegistry({})) == 0


# ── from_config() ────────────────────────────────────────────────


@pytest.mark.unit
class TestRegistryFromConfig:
    def test_from_config_with_factory_overrides(self) -> None:
        config = _make_config(driver="stub")
        providers = {"test-provider": config}

        registry = ProviderRegistry.from_config(
            providers,
            factory_overrides={"stub": _StubDriver},
        )

        assert "test-provider" in registry
        driver = registry.get("test-provider")
        assert isinstance(driver, _StubDriver)
        assert driver.provider_name == "test-provider"

    def test_from_config_multiple_providers(self) -> None:
        providers = {
            "alpha": _make_config(driver="stub"),
            "beta": _make_config(driver="stub"),
        }

        registry = ProviderRegistry.from_config(
            providers,
            factory_overrides={"stub": _StubDriver},
        )

        assert len(registry) == 2
        assert registry.list_providers() == ("alpha", "beta")

    def test_from_config_raises_for_unknown_driver(self) -> None:
        config = _make_config(driver="nonexistent")
        providers = {"test": config}

        with pytest.raises(DriverFactoryNotFoundError, match="No factory"):
            ProviderRegistry.from_config(providers)

    def test_from_config_empty_providers(self) -> None:
        registry = ProviderRegistry.from_config({})

        assert len(registry) == 0
        assert registry.list_providers() == ()

    def test_from_config_raises_for_non_callable_factory(self) -> None:
        config = _make_config(driver="bad")
        providers = {"test": config}

        with pytest.raises(DriverFactoryNotFoundError, match="not callable"):
            ProviderRegistry.from_config(
                providers,
                factory_overrides={"bad": "not-a-function"},
            )

    def test_from_config_raises_for_non_provider_return(self) -> None:
        config = _make_config(driver="bad")
        providers = {"test": config}

        with pytest.raises(
            DriverFactoryNotFoundError,
            match="BaseCompletionProvider",
        ):
            ProviderRegistry.from_config(
                providers,
                factory_overrides={
                    "bad": lambda name, cfg: "not-a-provider",
                },
            )

    def test_from_config_raises_for_factory_exception(self) -> None:
        """Factory that raises is wrapped as DriverFactoryNotFoundError."""
        config = _make_config(driver="bad")
        providers = {"test": config}

        def _failing_factory(name: str, cfg: ProviderConfig) -> BaseCompletionProvider:
            msg = "construction failed"
            raise TypeError(msg)

        with pytest.raises(
            DriverFactoryNotFoundError,
            match="Failed to instantiate",
        ):
            ProviderRegistry.from_config(
                providers,
                factory_overrides={"bad": _failing_factory},
            )

    def test_from_config_uses_litellm_by_default(self) -> None:
        """Default driver='litellm' resolves to LiteLLMDriver factory."""
        from synthorg.providers.drivers.litellm_driver import LiteLLMDriver

        config = _make_config(driver="litellm")
        providers = {"example-provider": config}

        registry = ProviderRegistry.from_config(providers)

        driver = registry.get("example-provider")
        assert isinstance(driver, LiteLLMDriver)


# ── Immutability ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRegistryImmutability:
    def test_registry_does_not_reflect_mutations_to_original_dict(self) -> None:
        drivers: dict[str, BaseCompletionProvider] = {
            "a": _StubDriver("a", _make_config()),
        }
        registry = ProviderRegistry(drivers)

        # Mutate the original dict
        drivers["b"] = _StubDriver("b", _make_config())

        # Registry should not be affected
        assert len(registry) == 1
        assert "b" not in registry


# ── Logging tests ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestRegistryLogging:
    def test_registry_built_event(self) -> None:
        stub = _StubDriver("test", _make_config())
        with structlog.testing.capture_logs() as cap:
            ProviderRegistry.from_config(
                {"test": _make_config()},
                factory_overrides={"litellm": lambda n, c: stub},
            )
        events = [e for e in cap if e.get("event") == PROVIDER_REGISTRY_BUILT]
        assert len(events) == 1
        assert events[0]["provider_count"] == 1

    def test_driver_not_registered_event(self) -> None:
        registry = ProviderRegistry({})
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(DriverNotRegisteredError),
        ):
            registry.get("nonexistent")
        events = [e for e in cap if e.get("event") == PROVIDER_DRIVER_NOT_REGISTERED]
        assert len(events) == 1
        assert events[0]["name"] == "nonexistent"
