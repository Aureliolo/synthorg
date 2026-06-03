"""Regression tests for boot-time service composition.

Two invariants the boot path must hold:

* ``_init_derived_services`` must compose ``ConfigResolver`` and the
  provider-management services into their slices; without that call
  every resolver-dependent endpoint 503s at boot.
  ``TestComposeSettingsDependentServices`` guards that they are wired
  into their slices.
* ``idempotency_service_of`` lazily composes the service from the
  persistence backend on first read; ``TestIdempotencyServiceLazyInit``
  guards the lazy wiring and the 503 when persistence is absent.
"""

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice, idempotency_service_of
from synthorg.api.lifecycle_helpers.settings_dependent_services import (
    compose_settings_dependent_services,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class TestComposeSettingsDependentServices:
    """``compose_settings_dependent_services`` wires the resolver + management."""

    def test_noop_when_settings_service_absent(self) -> None:
        app_state = make_app_state()
        compose_settings_dependent_services(app_state, None)
        # An empty / pre-settings boot composes nothing.
        assert app_state.slice(SettingsStateSlice).config_resolver is None
        assert app_state.slice(ProvidersStateSlice).management is None

    def test_composes_resolver_and_management(self) -> None:
        # The exact composition the dropped ``_init_derived_services``
        # used to perform; without it every resolver-dependent endpoint
        # 503s at boot (the production outage this guards).
        app_state = make_app_state()
        settings_service = mock_of[SettingsService]()
        compose_settings_dependent_services(app_state, settings_service)
        assert isinstance(config_resolver_of(app_state), ConfigResolver)
        assert app_state.slice(SettingsStateSlice).settings_service is settings_service
        assert app_state.slice(ProvidersStateSlice).management is not None
        assert app_state.slice(ApiCoreStateSlice).org_mutation_service is not None


class TestIdempotencyServiceLazyInit:
    """``idempotency_service_of`` lazy-composes from persistence, or 503s."""

    def test_lazy_composes_and_caches(self) -> None:
        app_state = make_app_state(persistence=mock_of[PersistenceBackend]())
        service = idempotency_service_of(app_state)
        assert service is not None
        # A second read returns the cached, slice-wired instance: the lazy
        # init wires into the slice rather than rebuilding on each call.
        assert idempotency_service_of(app_state) is service
        assert app_state.slice(ApiCoreStateSlice).idempotency_service is service

    def test_503_without_persistence(self) -> None:
        app_state = make_app_state()
        with pytest.raises(ServiceUnavailableError):
            idempotency_service_of(app_state)
