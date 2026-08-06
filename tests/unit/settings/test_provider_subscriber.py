"""Tests for ProviderSettingsSubscriber."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.providers.cassette import CassetteSession
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.errors import UnknownRoutingStrategyError
from synthorg.providers.routing.router import ModelRouter
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.provider_subscriber import (
    ProviderSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of


def _setting_value(value: str) -> SettingValue:
    """Build a SettingValue matching SettingsService.get() return type."""
    return SettingValue(
        namespace=SettingNamespace.PROVIDERS,
        key="routing_strategy",
        value=value,
        source=SettingSource.DEFAULT,
    )


def _make_state(config: RootConfig | None = None) -> AppState:
    cfg = config or RootConfig(company_name="test")
    router = ModelRouter(cfg.routing, dict(cfg.providers))
    return make_app_state(
        config=cfg,
        approval_store=ApprovalStore(),
        model_router=router,
    )


def _make_subscriber(
    config: RootConfig | None = None,
    app_state: AppState | None = None,
    settings_service: AsyncMock | None = None,
    get_return_value: str = "cost_aware",
) -> tuple[ProviderSettingsSubscriber, AppState]:
    cfg = config or RootConfig(company_name="test")
    state = app_state or _make_state(cfg)
    svc = settings_service or AsyncMock()
    if not settings_service:
        svc.get = AsyncMock(return_value=_setting_value(get_return_value))
    sub = ProviderSettingsSubscriber(
        config=cfg,
        app_state=state,
        settings_service=svc,
    )
    return sub, state


@pytest.mark.unit
class TestProviderSubscriberProtocol:
    """ProviderSettingsSubscriber conforms to SettingsSubscriber."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert ("providers", "routing_strategy") in sub.watched_keys
        assert ("providers", "retry_max_attempts") in sub.watched_keys

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "provider-settings"


@pytest.mark.unit
class TestProviderSubscriberRebuild:
    """on_settings_changed rebuilds ModelRouter when strategy changes."""

    async def test_routing_strategy_change_swaps_router(self) -> None:
        sub, state = _make_subscriber()
        old_router = state.slice(ProvidersStateSlice).model_router
        await sub.on_settings_changed("providers", "routing_strategy")
        assert state.slice(ProvidersStateSlice).model_router is not old_router

    async def test_rebuild_failure_propagates(self) -> None:
        """Errors in _rebuild_router propagate to the dispatcher."""
        sub, state = _make_subscriber(
            get_return_value="nonexistent_strategy",
        )
        old_router = state.slice(ProvidersStateSlice).model_router
        # Error propagates (dispatcher catches it for logging)
        with pytest.raises(UnknownRoutingStrategyError):
            await sub.on_settings_changed("providers", "routing_strategy")
        # Old router is still in place (swap never called)
        assert state.slice(ProvidersStateSlice).model_router is old_router

    async def test_retry_max_attempts_change_rebuilds_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry_max_attempts change rebuilds the registry + reloads runtime."""
        import synthorg.workers.runtime_builder as runtime_builder_mod

        reload_spy = AsyncMock()
        monkeypatch.setattr(runtime_builder_mod, "reload_runtime_services", reload_spy)
        cfg = RootConfig(company_name="test")
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=7),
            get_provider_configs=AsyncMock(return_value={}),
        )
        old_registry = ProviderRegistry({})
        state = make_app_state(
            config=cfg,
            approval_store=ApprovalStore(),
            model_router=ModelRouter(cfg.routing, dict(cfg.providers)),
            config_resolver=resolver,
            provider_registry=old_registry,
        )
        sub = ProviderSettingsSubscriber(
            config=cfg,
            app_state=state,
            settings_service=mock_of[SettingsService](),
        )
        await sub.on_settings_changed("providers", "retry_max_attempts")
        assert state.slice(ProvidersStateSlice).registry is not old_registry
        resolver.get_int.assert_awaited_once_with("providers", "retry_max_attempts")
        # The running engine captured the old registry; the runtime rebuild is
        # what makes the new cap reach the completion path. The trigger names
        # the key, so a reload in the log is traceable to the write behind it.
        reload_spy.assert_awaited_once_with(
            state, trigger="setting:providers.retry_max_attempts"
        )

    async def test_retry_change_skips_rebuild_during_cassette(self) -> None:
        """An active cassette session suppresses the registry rebuild."""
        cfg = RootConfig(company_name="test")
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=7),
            get_provider_configs=AsyncMock(return_value={}),
        )
        cassette_registry = ProviderRegistry(
            {}, cassette_session=mock_of[CassetteSession]()
        )
        state = make_app_state(
            config=cfg,
            approval_store=ApprovalStore(),
            model_router=ModelRouter(cfg.routing, dict(cfg.providers)),
            config_resolver=resolver,
            provider_registry=cassette_registry,
        )
        sub = ProviderSettingsSubscriber(
            config=cfg,
            app_state=state,
            settings_service=mock_of[SettingsService](),
        )
        await sub.on_settings_changed("providers", "retry_max_attempts")
        assert state.slice(ProvidersStateSlice).registry is cassette_registry
        resolver.get_int.assert_not_awaited()

    async def test_registry_rebuild_failure_preserves_old_registry(self) -> None:
        """A rebuild error re-raises and leaves the live registry untouched."""
        cfg = RootConfig(company_name="test")
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=7),
            get_provider_configs=AsyncMock(side_effect=RuntimeError("db down")),
        )
        old_registry = ProviderRegistry({})
        state = make_app_state(
            config=cfg,
            approval_store=ApprovalStore(),
            model_router=ModelRouter(cfg.routing, dict(cfg.providers)),
            config_resolver=resolver,
            provider_registry=old_registry,
        )
        sub = ProviderSettingsSubscriber(
            config=cfg,
            app_state=state,
            settings_service=mock_of[SettingsService](),
        )
        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("providers", "retry_max_attempts")
        assert state.slice(ProvidersStateSlice).registry is old_registry

    async def test_runtime_reload_failure_rolls_back_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reload failure after the swap restores the previous registry.

        The slice swap commits before the runtime reload; if the reload then
        raises, the slice and the running engine would otherwise be left on
        different registries. The subscriber must roll the swap back and re-heal
        the runtime, then propagate the original failure.
        """
        import synthorg.workers.runtime_builder as runtime_builder_mod

        calls: list[str] = []

        async def _reload(_state: object, *, trigger: str = "") -> None:
            calls.append(trigger)
            if len(calls) == 1:
                msg = "reload boom"
                raise RuntimeError(msg)

        monkeypatch.setattr(runtime_builder_mod, "reload_runtime_services", _reload)
        cfg = RootConfig(company_name="test")
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=7),
            get_provider_configs=AsyncMock(return_value={}),
        )
        old_registry = ProviderRegistry({})
        state = make_app_state(
            config=cfg,
            approval_store=ApprovalStore(),
            model_router=ModelRouter(cfg.routing, dict(cfg.providers)),
            config_resolver=resolver,
            provider_registry=old_registry,
        )
        sub = ProviderSettingsSubscriber(
            config=cfg,
            app_state=state,
            settings_service=mock_of[SettingsService](),
        )
        with pytest.raises(RuntimeError, match="reload boom"):
            await sub.on_settings_changed("providers", "retry_max_attempts")
        # Swap rolled back to the original registry, and the runtime was
        # re-healed (a second reload) so engine + slice stay consistent.
        assert state.slice(ProvidersStateSlice).registry is old_registry
        # The two reloads are distinguishable in the logs: the rollback names
        # itself, so a heal is never mistaken for the write that failed.
        assert calls == [
            "setting:providers.retry_max_attempts",
            "setting:providers.retry_max_attempts-rollback",
        ]

    async def test_runtime_reload_failure_rolls_back_to_unset_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reload failure with no prior registry restores the unset state.

        When the slice had no registry before the swap, rolling back must clear
        the slice back to ``None`` rather than leaving the newly swapped
        registry committed while the engine never adopted it.
        """
        import synthorg.workers.runtime_builder as runtime_builder_mod

        calls: list[str] = []

        async def _reload(_state: object, *, trigger: str = "") -> None:
            calls.append(trigger)
            if len(calls) == 1:
                msg = "reload boom"
                raise RuntimeError(msg)

        monkeypatch.setattr(runtime_builder_mod, "reload_runtime_services", _reload)
        cfg = RootConfig(company_name="test")
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(return_value=7),
            get_provider_configs=AsyncMock(return_value={}),
        )
        # No provider_registry: the slice starts with registry unset (None).
        state = make_app_state(
            config=cfg,
            approval_store=ApprovalStore(),
            model_router=ModelRouter(cfg.routing, dict(cfg.providers)),
            config_resolver=resolver,
        )
        sub = ProviderSettingsSubscriber(
            config=cfg,
            app_state=state,
            settings_service=mock_of[SettingsService](),
        )
        with pytest.raises(RuntimeError, match="reload boom"):
            await sub.on_settings_changed("providers", "retry_max_attempts")
        # Rolled back to the unset state, not left on the swapped registry.
        assert state.slice(ProvidersStateSlice).registry is None
        # The two reloads are distinguishable in the logs: the rollback names
        # itself, so a heal is never mistaken for the write that failed.
        assert calls == [
            "setting:providers.retry_max_attempts",
            "setting:providers.retry_max_attempts-rollback",
        ]

    async def test_settings_service_failure_preserves_old_router(self) -> None:
        """When SettingsService.get() fails, old router stays in place."""
        svc = AsyncMock()
        svc.get = AsyncMock(side_effect=RuntimeError("db down"))
        sub, state = _make_subscriber(settings_service=svc)
        old_router = state.slice(ProvidersStateSlice).model_router
        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("providers", "routing_strategy")
        assert state.slice(ProvidersStateSlice).model_router is old_router
