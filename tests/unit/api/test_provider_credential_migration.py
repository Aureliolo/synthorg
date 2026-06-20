"""Unit tests for the embedded-provider-key -> catalog boot migration.

Covers the core rewrite logic of ``_migrate_configs``: minting a catalog
connection for an embedded ``api_key`` and rewriting onto ``connection_name``,
the idempotent skip for already-migrated configs, the catalog-absent failure
path (mint raises -> the config is left untouched, no crash), and the
security invariant that the key value is never logged.
"""

import pytest
import structlog

import synthorg.api.lifecycle_helpers.provider_credential_migration as migration
from synthorg.api.state import AppState
from synthorg.providers.errors import ProviderValidationError
from tests._shared import mock_of

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
