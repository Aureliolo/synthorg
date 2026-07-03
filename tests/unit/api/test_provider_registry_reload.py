"""Boot-time provider-registry reload from persisted configs.

A restarted, already-set-up deployment must come back with its
DB-persisted providers live; only ``/setup/complete`` and provider
mutations used to rebuild the registry, so every restart booted into
empty-company mode with all provider-gated features unwired.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.provider_registry_reload import (
    reload_persisted_provider_registry,
)
from synthorg.api.state import AppState
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.providers.state import ProvidersStateSlice, has_active_provider
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_state() -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
    )


def _resolver(
    configs: dict[str, ProviderConfig],
) -> ConfigResolver:
    async def _get_provider_configs() -> dict[str, ProviderConfig]:
        return configs

    async def _get_int(namespace: str, key: str) -> int:
        msg = f"{namespace}.{key} not registered"
        raise SettingNotFoundError(msg)

    return mock_of[ConfigResolver](  # type: ignore[no-any-return]
        get_provider_configs=_get_provider_configs,
        get_int=_get_int,
    )


class TestReloadPersistedProviderRegistry:
    async def test_swaps_registry_from_persisted_configs(self) -> None:
        state = _make_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=_resolver(
                {
                    "test-provider": ProviderConfig(
                        driver="scripted", connection_name="conn-scripted"
                    )
                }
            ),
        )
        state.wire(
            IntegrationsStateSlice,
            provider_credential_catalog=mock_of[ConnectionCatalog](),
        )

        registry = await reload_persisted_provider_registry(state)

        assert registry is not None
        assert state.slice(ProvidersStateSlice).registry is registry
        assert has_active_provider(state)
        assert registry.list_providers() == ("test-provider",)

    async def test_no_resolver_is_a_noop(self) -> None:
        state = _make_state()
        assert await reload_persisted_provider_registry(state) is None
        assert state.slice(ProvidersStateSlice).registry is None

    async def test_no_persisted_configs_is_a_noop(self) -> None:
        """A genuine first-run empty company keeps the empty-company boot."""
        state = _make_state()
        state.wire(SettingsStateSlice, config_resolver=_resolver({}))

        assert await reload_persisted_provider_registry(state) is None
        assert state.slice(ProvidersStateSlice).registry is None
        assert not has_active_provider(state)
