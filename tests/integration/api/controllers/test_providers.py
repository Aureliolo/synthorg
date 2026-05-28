"""Integration tests for provider controller -- DB override behavior."""

import json

import pytest

from synthorg.config.schema import RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend


@pytest.fixture
async def fake_persistence() -> FakePersistenceBackend:
    """In-memory persistence backend."""
    backend = FakePersistenceBackend()
    await backend.connect()
    return backend


@pytest.fixture
async def fake_message_bus() -> FakeMessageBus:
    """In-memory message bus."""
    bus = FakeMessageBus()
    await bus.start()
    return bus


@pytest.mark.integration
class TestProviderControllerDbOverride:
    """Test that DB-stored settings override YAML providers."""

    async def test_db_providers_override_config(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.app import create_app
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(company_name="test")
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        from cryptography.fernet import Fernet

        from synthorg.settings.encryption import SettingsEncryptor

        encryptor = SettingsEncryptor(Fernet.generate_key())
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
            encryptor=encryptor,
        )

        db_providers = {
            "db-provider": {"driver": "litellm"},
        }
        await settings_service.set("providers", "configs", json.dumps(db_providers))

        app = create_app(
            config=config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=CostTracker(),
            auth_service=auth_service,
            settings_service=settings_service,
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/providers")
            assert resp.status_code == 200
            body = resp.json()
            # /providers returns a paginated list; locate the provider
            # by its embedded ``name`` field.
            providers_by_name = {p["name"]: p for p in body["data"]}
            assert "db-provider" in providers_by_name
            assert providers_by_name["db-provider"]["driver"] == "litellm"
            assert providers_by_name["db-provider"]["auth_type"] == "api_key"

            detail_resp = await client.get("/api/v1/providers/db-provider")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["data"]["driver"] == "litellm"
            assert "api_key" not in detail["data"]
