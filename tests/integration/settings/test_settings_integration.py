"""Integration test: API settings round-trip through real persistence.

Uses a real SQLite backend + SettingsService + ConfigResolver to
verify that DB overrides flow through the full resolution chain.
"""

from typing import TYPE_CHECKING

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.config.schema import RootConfig
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary on-disk database path."""
    return str(tmp_path / "settings-test.db")


@pytest.fixture
async def backend(
    db_path: str,
    tmp_path: Path,
) -> AsyncGenerator[SQLitePersistenceBackend]:
    """Create a connected and migrated on-disk SQLite backend.

    Uses an isolated revisions copy + ``--skip-lock`` so parallel
    xdist workers never contend on Atlas's directory lock.

    Yields:
        A ``SQLitePersistenceBackend`` ready for settings operations.
    """
    be = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await be.connect()
    revisions_path = migrations.copy_revisions(
        tmp_path / "sqlite_revisions",
        backend="sqlite",
    )
    await migrations.migrate_apply(
        migrations.to_sqlite_url(db_path),
        revisions_path=revisions_path,
        backend="sqlite",
    )
    yield be
    await be.disconnect()


@pytest.fixture
def config() -> RootConfig:
    """Minimal root config with defaults."""
    return RootConfig(company_name="test-co")


@pytest.fixture
def settings_service(
    backend: SQLitePersistenceBackend,
    config: RootConfig,
) -> SettingsService:
    """Real SettingsService wired to on-disk SQLite."""
    return SettingsService(
        repository=backend.settings,
        registry=get_registry(),
        config=config,
    )


@pytest.fixture
def resolver(
    settings_service: SettingsService,
    config: RootConfig,
) -> ConfigResolver:
    """Real ConfigResolver wired to real SettingsService."""
    return ConfigResolver(
        settings_service=settings_service,
        config=config,
    )


@pytest.mark.integration
class TestApiSettingsIntegration:
    """End-to-end test: DB override → SettingsService → ConfigResolver."""

    async def test_db_override_flows_through_resolver(
        self,
        settings_service: SettingsService,
        resolver: ConfigResolver,
    ) -> None:
        """Verify DB overrides flow through the full resolution chain.

        Writes a rate-limit override via ``SettingsService`` and reads
        it back through ``ConfigResolver.get_api_config()``.
        """
        await settings_service.set(
            "api",
            "rate_limit_unauth_max_requests",
            "50",
        )
        await settings_service.set(
            "api",
            "rate_limit_auth_max_requests",
            "1000",
        )

        result = await resolver.get_api_config()

        assert result.rate_limit.unauth_max_requests == 50
        assert result.rate_limit.auth_max_requests == 1000
        # Non-overridden fields keep code defaults
        assert result.rate_limit.time_unit.value == "minute"
        assert result.auth.jwt_expiry_minutes == 1440
        assert result.auth.min_password_length == 12

    async def test_defaults_without_db_overrides(
        self,
        resolver: ConfigResolver,
    ) -> None:
        """Verify code defaults are used when no DB overrides exist.

        All five runtime-editable API settings should resolve to their
        ``SettingDefinition`` defaults.
        """
        result = await resolver.get_api_config()

        assert result.rate_limit.unauth_max_requests == 20
        assert result.rate_limit.auth_max_requests == 6000
        assert result.rate_limit.time_unit.value == "minute"
        assert result.auth.jwt_expiry_minutes == 1440
        assert result.auth.min_password_length == 12

    async def test_ws_auth_timeout_override_flows_through_bridge(
        self,
        settings_service: SettingsService,
        resolver: ConfigResolver,
    ) -> None:
        """Operator override of ``ws_auth_timeout_seconds`` surfaces on the bridge.

        Verifies the restart-required kill-switch wiring: operator writes a
        value via ``SettingsService``, it reaches ``ApiBridgeConfig`` via
        ``ConfigResolver.get_api_bridge_config`` (the method
        ``_apply_bridge_config`` calls at startup).
        """
        await settings_service.set("api", "ws_auth_timeout_seconds", "3.5")

        bridge = await resolver.get_api_bridge_config()

        assert bridge.ws_auth_timeout_seconds == pytest.approx(3.5)

    async def test_ws_auth_timeout_default_on_bridge(
        self,
        resolver: ConfigResolver,
    ) -> None:
        """Default ``ws_auth_timeout_seconds`` is 10.0 when no override exists."""
        bridge = await resolver.get_api_bridge_config()

        assert bridge.ws_auth_timeout_seconds == pytest.approx(10.0)
