"""Tests for AppState service swap methods (hot-reload)."""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.router import ModelRouter


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, object] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return AppState(**defaults)  # type: ignore[arg-type]


def _make_registry() -> ProviderRegistry:
    """Build an empty ProviderRegistry."""
    return ProviderRegistry({})


def _make_router(config: RootConfig | None = None) -> ModelRouter:
    """Build a ModelRouter from default config."""
    cfg = config or RootConfig(company_name="test")
    return ModelRouter(cfg.routing, dict(cfg.providers))


@pytest.mark.unit
class TestAppStateProviderRegistrySwap:
    """Tests for provider_registry slot and swap."""

    def test_provider_registry_raises_when_none(self) -> None:
        state = _make_state(provider_registry=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.provider_registry

    def test_provider_registry_returns_when_set(self) -> None:
        registry = _make_registry()
        state = _make_state(provider_registry=registry)
        assert state.provider_registry is registry

    def test_has_provider_registry_false_when_none(self) -> None:
        state = _make_state()
        assert state.has_provider_registry is False

    def test_has_provider_registry_true_when_set(self) -> None:
        registry = _make_registry()
        state = _make_state(provider_registry=registry)
        assert state.has_provider_registry is True

    def test_swap_provider_registry_replaces_reference(self) -> None:
        old = _make_registry()
        new = _make_registry()
        state = _make_state(provider_registry=old)
        state.swap_provider_registry(new)
        assert state.provider_registry is new
        assert state.provider_registry is not old

    def test_swap_provider_registry_from_none(self) -> None:
        new = _make_registry()
        state = _make_state()
        state.swap_provider_registry(new)
        assert state.provider_registry is new


@pytest.mark.unit
class TestAppStateModelRouterSwap:
    """Tests for model_router slot and swap."""

    def test_model_router_raises_when_none(self) -> None:
        state = _make_state(model_router=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.model_router

    def test_model_router_returns_when_set(self) -> None:
        router = _make_router()
        state = _make_state(model_router=router)
        assert state.model_router is router

    def test_has_model_router_false_when_none(self) -> None:
        state = _make_state()
        assert state.has_model_router is False

    def test_has_model_router_true_when_set(self) -> None:
        router = _make_router()
        state = _make_state(model_router=router)
        assert state.has_model_router is True

    def test_swap_model_router_replaces_reference(self) -> None:
        old = _make_router()
        new = _make_router()
        state = _make_state(model_router=old)
        state.swap_model_router(new)
        assert state.model_router is new
        assert state.model_router is not old

    def test_swap_model_router_from_none(self) -> None:
        new = _make_router()
        state = _make_state()
        state.swap_model_router(new)
        assert state.model_router is new
