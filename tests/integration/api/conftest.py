"""Fixtures shared by tests under ``tests/integration/api/``.

Provides an on-disk SQLite persistence backend fixture mirroring the
one in ``tests/integration/persistence/conftest.py`` so API-level
integration tests can exercise real persistence without duplicating
bootstrap boilerplate in every test module.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from synthorg.api.app import create_app
from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _runtime_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", TEST_SETTINGS_KEY)


@pytest.fixture
async def fake_persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def fake_message_bus() -> AsyncGenerator[FakeMessageBus]:
    bus = FakeMessageBus()
    await bus.start()
    yield bus
    await bus.stop()


def build_runtime_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    *,
    with_provider: bool,
    company_name: str,
    coordinator: Any = None,
) -> Any:
    """Build an app for provider-present-switch integration tests.

    ``with_provider`` registers one scripted provider so the company is
    not empty; otherwise no registry is passed and task creation is
    rejected at the controller. ``coordinator`` injects an explicit
    coordinator so the injection-over-autowire convention can be tested.
    """
    root_config = RootConfig(company_name=company_name)
    auth_service = AuthService(
        root_config.api.auth.model_copy(update={"jwt_secret": TEST_JWT_SECRET}),
    )
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    provider_registry = (
        ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")},
        )
        if with_provider
        else None
    )
    return create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        agent_registry=AgentRegistryService(),
        settings_service=settings_service,
        provider_registry=provider_registry,
        coordinator=coordinator,
    )


async def _isolated_sqlite_migrate(db_path: str, tmp_path: Path) -> None:
    """Apply SQLite migrations against a per-test isolated revisions copy.

    Each xdist worker has its own SQLite file (per ``tmp_path``), so
    yoyo's DB-level lock cannot contend across workers; the per-test
    revisions copy keeps the on-disk layout symmetric with production.
    """
    revisions_path = migrations.copy_revisions(
        tmp_path / f"sqlite_revisions_{uuid.uuid4().hex}",
        backend="sqlite",
    )
    await migrations.migrate_apply(
        migrations.to_sqlite_url(db_path),
        revisions_path=revisions_path,
        backend="sqlite",
    )


@pytest.fixture
async def on_disk_backend(
    tmp_path: Path,
) -> AsyncGenerator[SQLitePersistenceBackend]:
    """Connected + migrated on-disk SQLite backend for API integration tests."""
    db_path = str(tmp_path / "test.db")
    backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await backend.connect()
    try:
        await _isolated_sqlite_migrate(db_path, tmp_path)
        yield backend
    finally:
        await backend.disconnect()
