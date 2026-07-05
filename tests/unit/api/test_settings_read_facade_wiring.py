"""Unit tests for the settings- + provider-read MCP facade wiring.

Covers the two read-facade wirers built during settings composition:
``_wire_settings_read_facade`` (wraps the settings service) and
``_wire_provider_read_facade`` (wraps the provider registry + health +
management service). Each is idempotent and gated on its dependencies.
"""

import pytest

from synthorg.api.lifecycle_helpers.settings_dependent_services import (
    _wire_provider_read_facade,
    _wire_settings_read_facade,
)
from synthorg.api.state import AppState
from synthorg.infrastructure.services import ProviderReadService, SettingsReadService
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class TestSettingsReadFacadeWiring:
    async def test_wires_facade(self) -> None:
        app_state = make_app_state()
        settings = mock_of[SettingsService]()
        _wire_settings_read_facade(app_state, settings)
        assert app_state.slice(SettingsStateSlice).settings_read_service is not None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = make_app_state()
        existing = SettingsReadService(settings=mock_of[SettingsService]())
        app_state.wire(SettingsStateSlice, settings_read_service=existing)
        _wire_settings_read_facade(app_state, mock_of[SettingsService]())
        assert app_state.slice(SettingsStateSlice).settings_read_service is existing


class TestProviderReadFacadeWiring:
    def _app_state(
        self,
        *,
        with_registry: bool = True,
        with_health: bool = True,
    ) -> AppState:
        fields: dict[str, object] = {}
        if with_registry:
            fields["registry"] = mock_of[ProviderRegistry]()
        if with_health:
            fields["health_tracker"] = mock_of[ProviderHealthTracker]()
        return make_app_state(slices={ProvidersStateSlice: fields})

    async def test_wires_facade_with_registry_and_health(self) -> None:
        app_state = self._app_state()
        _wire_provider_read_facade(app_state, mock_of[ProviderManagementService]())
        assert app_state.slice(FacadesStateSlice).provider_read_service is not None

    async def test_absent_without_registry(self) -> None:
        app_state = self._app_state(with_registry=False)
        _wire_provider_read_facade(app_state, mock_of[ProviderManagementService]())
        assert app_state.slice(FacadesStateSlice).provider_read_service is None

    async def test_absent_without_health(self) -> None:
        app_state = self._app_state(with_health=False)
        _wire_provider_read_facade(app_state, mock_of[ProviderManagementService]())
        assert app_state.slice(FacadesStateSlice).provider_read_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = self._app_state()
        existing = ProviderReadService(
            registry=mock_of[ProviderRegistry](),
            health=mock_of[ProviderHealthTracker](),
            management=mock_of[ProviderManagementService](),
        )
        app_state.wire(FacadesStateSlice, provider_read_service=existing)
        _wire_provider_read_facade(app_state, mock_of[ProviderManagementService]())
        assert app_state.slice(FacadesStateSlice).provider_read_service is existing
