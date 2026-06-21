"""Unit tests for the embedded-provider-key -> catalog boot migration.

Covers the core rewrite logic of ``_migrate_configs``: minting a catalog
connection for an embedded ``api_key`` and rewriting onto ``connection_name``,
the idempotent skip for already-migrated configs, the catalog-absent failure
path (mint raises -> the config is left untouched, no crash), and the
security invariant that the key value is never logged. The top-level
``migrate_embedded_provider_keys`` cases cover the envelope unwrap/rewrite
round-trip and the pre-envelope bare-dict upgrade.
"""

import json
from collections.abc import AsyncIterator

import pytest
import structlog

import synthorg.api.lifecycle_helpers.provider_credential_migration as migration
import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.providers.errors import ProviderValidationError
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_SECRET = "sk-super-secret-value-1234"
_STORE_PATH = "synthorg.providers.management._credential_helpers.store_provider_api_key"


class TestMigrateConfigs:
    async def test_mints_and_rewrites_embedded_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _store(_app: object, name: str, _key: str) -> str:
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        configs: dict[str, object] = {
            "example-provider": {"auth_type": "api_key", "api_key": _SECRET},
        }
        migrated, failed, changed = await migration._migrate_configs(
            mock_of[AppState](), configs
        )

        assert migrated == 1
        assert failed == 0
        assert changed is True
        conf = configs["example-provider"]
        assert isinstance(conf, dict)
        assert conf["connection_name"] == "provider-example-provider"
        assert "api_key" not in conf

    async def test_idempotent_skip_when_already_migrated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def _store(_app: object, name: str, _key: str) -> str:
            calls.append(name)
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        configs: dict[str, object] = {
            "already": {"auth_type": "api_key", "connection_name": "provider-already"},
            "no-secret": {"auth_type": "none"},
        }
        migrated, failed, changed = await migration._migrate_configs(
            mock_of[AppState](), configs
        )

        assert migrated == 0
        assert failed == 0
        # No mint and no scrub (neither config carried an embedded api_key),
        # so there is nothing to persist.
        assert changed is False
        assert calls == []

    async def test_scrub_only_rewrite_is_reported_as_changed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A config already migrated onto a connection_name but still carrying a
        # stray plaintext api_key must be scrubbed AND reported as changed so
        # the caller persists the cleaned config (no mint happens here).
        calls: list[str] = []

        async def _store(_app: object, name: str, _key: str) -> str:
            calls.append(name)
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        configs: dict[str, object] = {
            "stale": {
                "auth_type": "api_key",
                "connection_name": "provider-stale",
                "api_key": _SECRET,
            },
        }
        migrated, failed, changed = await migration._migrate_configs(
            mock_of[AppState](), configs
        )

        assert migrated == 0
        assert failed == 0
        assert changed is True
        assert calls == []
        conf = configs["stale"]
        assert isinstance(conf, dict)
        assert "api_key" not in conf
        assert conf["connection_name"] == "provider-stale"

    async def test_catalog_absent_leaves_config_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _store(_app: object, _name: str, _key: str) -> str:
            msg = "no credential catalog is available"
            raise ProviderValidationError(msg)

        monkeypatch.setattr(_STORE_PATH, _store)
        configs: dict[str, object] = {
            "example-provider": {"auth_type": "api_key", "api_key": _SECRET},
        }
        migrated, failed, changed = await migration._migrate_configs(
            mock_of[AppState](), configs
        )

        # Mint failed: nothing migrated, the embedded key is left in place
        # (not popped) so the next boot can retry without losing it.
        assert migrated == 0
        assert failed == 1
        # The embedded key is left in place for retry, so nothing is scrubbed.
        assert changed is False
        conf = configs["example-provider"]
        assert isinstance(conf, dict)
        assert "connection_name" not in conf
        assert conf["api_key"] == _SECRET

    async def test_key_value_never_logged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _store(_app: object, name: str, _key: str) -> str:
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        configs: dict[str, object] = {
            "example-provider": {"auth_type": "api_key", "api_key": _SECRET},
        }
        with structlog.testing.capture_logs() as logs:
            await migration._migrate_configs(mock_of[AppState](), configs)

        assert _SECRET not in repr(logs)


@pytest.fixture
async def persistence() -> AsyncIterator[FakePersistenceBackend]:
    """Connected in-memory persistence backend."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def settings_service(persistence: FakePersistenceBackend) -> SettingsService:
    """SettingsService wired to fake persistence and a fresh registry."""
    from cryptography.fernet import Fernet

    return SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
        encryptor=SettingsEncryptor(Fernet.generate_key()),
    )


@pytest.fixture
def app_state(
    persistence: FakePersistenceBackend,
    settings_service: SettingsService,
) -> AppState:
    """AppState with a connected backend and a wired settings service."""
    return make_app_state(
        config=RootConfig(company_name="test-company"),
        persistence=persistence,
        settings_service=settings_service,
    )


class TestMigrateTopLevel:
    async def test_envelope_inner_key_is_migrated_and_rewritten(
        self,
        app_state: AppState,
        settings_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An embedded key inside the versioned envelope is migrated in place."""

        async def _store(_app: object, name: str, _key: str) -> str:
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        await settings_service.set(
            "providers",
            "configs",
            json.dumps(
                {
                    "schema_version": 1,
                    "providers": {
                        "example-provider": {
                            "auth_type": "api_key",
                            "api_key": _SECRET,
                        },
                    },
                },
            ),
        )

        await migration.migrate_embedded_provider_keys(app_state)

        stored = json.loads((await settings_service.get("providers", "configs")).value)
        assert stored["schema_version"] == 1
        inner = stored["providers"]["example-provider"]
        assert inner["connection_name"] == "provider-example-provider"
        assert "api_key" not in inner

    async def test_pre_envelope_bare_dict_is_upgraded_to_envelope(
        self,
        app_state: AppState,
        settings_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A legacy bare provider dict is rewritten in envelope form.

        Nothing needs migrating (the provider is already on a
        ``connection_name``), but the resolver only accepts the versioned
        envelope, so the bare dict is upgraded on the same pass.
        """

        async def _store(_app: object, name: str, _key: str) -> str:
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        await settings_service.set(
            "providers",
            "configs",
            json.dumps(
                {
                    "example-provider": {
                        "auth_type": "api_key",
                        "connection_name": "provider-example-provider",
                    },
                },
            ),
        )

        await migration.migrate_embedded_provider_keys(app_state)

        stored = json.loads((await settings_service.get("providers", "configs")).value)
        assert stored["schema_version"] == 1
        assert "example-provider" in stored["providers"]

    async def test_future_schema_version_is_left_untouched(
        self,
        app_state: AppState,
        settings_service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A newer-than-current envelope is skipped, not downgraded.

        Unwrapping a future ``schema_version`` and rewriting it with the
        current version would silently downgrade a newer on-disk format, so
        the hook leaves the blob untouched and logs a warning instead.
        """

        async def _store(_app: object, name: str, _key: str) -> str:
            return f"provider-{name}"

        monkeypatch.setattr(_STORE_PATH, _store)
        future_blob = json.dumps(
            {
                "schema_version": 999,
                "providers": {
                    "example-provider": {
                        "auth_type": "api_key",
                        "api_key": _SECRET,
                    },
                },
            },
        )
        await settings_service.set("providers", "configs", future_blob)

        with structlog.testing.capture_logs() as logs:
            await migration.migrate_embedded_provider_keys(app_state)

        # The blob is unchanged: still version 999 with the embedded key
        # intact (no migration, no version downgrade).
        stored = json.loads((await settings_service.get("providers", "configs")).value)
        assert stored["schema_version"] == 999
        assert stored["providers"]["example-provider"]["api_key"] == _SECRET
        phases = [
            e.get("phase")
            for e in logs
            if e.get("event") == migration.PROVIDER_CREDENTIAL_MIGRATION_FAILED
        ]
        assert "unsupported_schema_version" in phases
