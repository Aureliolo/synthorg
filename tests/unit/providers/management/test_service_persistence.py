"""Persistence hardening tests for ProviderManagementService.

Covers the versioned ``providers.configs`` envelope round-trip and
fallbacks, the split serialise-vs-DB-write failure types, and the
atomic hot-reload rollback (a swap failure rolls the persisted blob
back so the database and the running registry never diverge).
"""

import json

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.observability.events.provider import PROVIDER_HOT_RELOAD_FAILED
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.providers.errors import (
    ProviderPersistenceError,
    ProviderSerializationError,
)
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingSource
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

from .conftest import make_create_request

pytestmark = pytest.mark.unit

_SERIALIZE_PATH = (
    "synthorg.providers.management._persistence.serialize_provider_envelope"
)


def _raise_swap(_registry: ProviderRegistry) -> None:
    """Stand-in for ``swap_provider_registry`` that always fails."""
    msg = "swap boom"
    raise RuntimeError(msg)


class TestEnvelopeRoundTrip:
    async def test_created_provider_reads_back_through_envelope(
        self,
        service: ProviderManagementService,
        app_state: AppState,
    ) -> None:
        """A created provider survives the envelope write/read cycle."""
        await service.create_provider(make_create_request(name="example-provider"))

        configs = await config_resolver_of(app_state).get_provider_configs()
        assert "example-provider" in configs

    async def test_stored_blob_is_versioned_envelope(
        self,
        service: ProviderManagementService,
        settings_service: SettingsService,
    ) -> None:
        """The persisted blob is wrapped in a schema-versioned envelope."""
        await service.create_provider(make_create_request(name="example-provider"))

        stored = json.loads((await settings_service.get("providers", "configs")).value)
        assert stored["schema_version"] == 1
        assert "example-provider" in stored["providers"]


class TestEnvelopeReadFallbacks:
    async def test_unknown_schema_version_falls_back_with_warning(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        """A blob stamped with an unknown version falls back to defaults."""
        await settings_service.set(
            "providers",
            "configs",
            json.dumps({"schema_version": 999, "providers": {}}),
        )

        with structlog.testing.capture_logs() as logs:
            configs = await config_resolver_of(app_state).get_provider_configs()

        assert dict(configs) == {}
        reasons = [
            e["reason"]
            for e in logs
            if e.get("event") == SETTINGS_FETCH_FAILED and "reason" in e
        ]
        assert "unknown_schema_version" in reasons

    async def test_legacy_bare_dict_falls_back_with_warning(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        """A pre-envelope bare provider dict fails envelope validation."""
        await settings_service.set(
            "providers",
            "configs",
            json.dumps({"example-provider": {"driver": "litellm"}}),
        )

        with structlog.testing.capture_logs() as logs:
            configs = await config_resolver_of(app_state).get_provider_configs()

        assert dict(configs) == {}
        reasons = [
            e["reason"]
            for e in logs
            if e.get("event") == SETTINGS_FETCH_FAILED and "reason" in e
        ]
        assert "invalid_schema_fallback" in reasons


class TestSplitFailureTypes:
    async def test_serialise_failure_raises_serialization_error(
        self,
        service: ProviderManagementService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A serialise failure is distinct from a DB-write failure."""

        def _boom(_providers: object) -> str:
            msg = "cannot serialise"
            raise RuntimeError(msg)

        monkeypatch.setattr(_SERIALIZE_PATH, _boom)
        with pytest.raises(ProviderSerializationError):
            await service.create_provider(make_create_request())

    async def test_db_write_failure_raises_persistence_error(
        self,
        service: ProviderManagementService,
        settings_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DB-write failure surfaces as a persistence error, not validation."""
        original_set = settings_service.set

        async def _set(namespace: str, key: str, value: str) -> None:
            if (namespace, key) == ("providers", "configs"):
                msg = "db down"
                raise RuntimeError(msg)
            await original_set(namespace, key, value)

        monkeypatch.setattr(settings_service, "set", _set)
        with pytest.raises(ProviderPersistenceError):
            await service.create_provider(make_create_request())


class TestHotReloadRollback:
    async def test_swap_failure_rolls_back_blob_and_alerts(
        self,
        service: ProviderManagementService,
        app_state: AppState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hot-reload swap failure rolls the persisted blob back + alerts.

        After creating provider A, a forced swap failure on creating B must
        restore the DB to the A-only state (no divergence) and raise
        ``ProviderPersistenceError`` with an ERROR alert.
        """
        await service.create_provider(make_create_request(name="provider-a"))

        monkeypatch.setattr(app_state, "swap_provider_registry", _raise_swap)

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ProviderPersistenceError),
        ):
            await service.create_provider(make_create_request(name="provider-b"))

        # The persisted blob is rolled back to the pre-write (A-only) state.
        configs = await config_resolver_of(app_state).get_provider_configs()
        assert "provider-a" in configs
        assert "provider-b" not in configs

        alerts = [e for e in logs if e.get("event") == PROVIDER_HOT_RELOAD_FAILED]
        assert alerts
        assert alerts[0]["log_level"] == "error"
        assert alerts[0].get("rolled_back") is True

    async def test_swap_failure_on_first_write_deletes_row(
        self,
        service: ProviderManagementService,
        app_state: AppState,
        settings_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no prior row existed, the rollback deletes the fresh row."""
        monkeypatch.setattr(app_state, "swap_provider_registry", _raise_swap)

        with pytest.raises(ProviderPersistenceError):
            await service.create_provider(make_create_request(name="provider-a"))

        entry = await settings_service.get_entry("providers", "configs")
        assert entry.source != SettingSource.DATABASE
