"""Integration tests for provider controller -- DB override behavior."""

import json
from collections.abc import AsyncIterator

import pytest
from litestar import Litestar

from synthorg.config.schema import RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend


async def _build_app_with_db_providers(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    db_providers: dict[str, dict[str, str]],
) -> Litestar:
    """Build an app whose settings DB stores ``db_providers``."""
    from cryptography.fernet import Fernet

    from synthorg.api.auth.service import AuthService
    from synthorg.budget.tracker import CostTracker
    from synthorg.settings.encryption import SettingsEncryptor
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

    config = RootConfig(company_name="test")
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    encryptor = SettingsEncryptor(Fernet.generate_key())
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
        encryptor=encryptor,
    )
    from synthorg.config.provider_configs_read import PROVIDERS_CONFIG_SCHEMA_VERSION

    await settings_service.set(
        "providers",
        "configs",
        json.dumps(
            {
                "schema_version": PROVIDERS_CONFIG_SCHEMA_VERSION,
                "providers": db_providers,
            },
        ),
    )
    return create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
    )


@pytest.fixture
async def fake_persistence() -> AsyncIterator[FakePersistenceBackend]:
    """In-memory persistence backend, disconnected on teardown."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def fake_message_bus() -> AsyncIterator[FakeMessageBus]:
    """In-memory message bus, stopped on teardown."""
    bus = FakeMessageBus()
    await bus.start()
    yield bus
    await bus.stop()


@pytest.mark.integration
class TestProviderControllerDbOverride:
    """Test that DB-stored settings override YAML providers."""

    async def test_db_providers_override_config(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        app = await _build_app_with_db_providers(
            fake_persistence,
            fake_message_bus,
            {
                "test-provider": {
                    "driver": "litellm",
                    "connection_name": "provider-test",
                }
            },
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/providers")
            assert resp.status_code == 200
            body = resp.json()
            # /providers returns a paginated list; locate the provider
            # by its embedded ``name`` field.
            providers_by_name = {p["name"]: p for p in body["data"]}
            assert "test-provider" in providers_by_name
            assert providers_by_name["test-provider"]["driver"] == "litellm"
            assert providers_by_name["test-provider"]["auth_type"] == "api_key"

            detail_resp = await client.get("/api/v1/providers/test-provider")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["data"]["driver"] == "litellm"
            assert "api_key" not in detail["data"]
            # Single-resource reads now advertise the canonical name too.
            assert detail["data"]["name"] == "test-provider"


@pytest.mark.integration
class TestProviderConfigDiagnosticsEndpoint:
    """Test what ``GET /providers/config-diagnostics`` tells an operator."""

    async def test_rejected_entry_is_confined_to_that_entry(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        app = await _build_app_with_db_providers(
            fake_persistence,
            fake_message_bus,
            {
                "serving-provider": {
                    "driver": "litellm",
                    "connection_name": "provider-serving",
                },
                # A key this build no longer accepts. The entry is refused;
                # the question is what it costs its neighbour.
                "stale-provider": {
                    "driver": "litellm",
                    "connection_name": "provider-stale",
                    "retired_key": "value",
                },
            },
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/providers/config-diagnostics")
            assert resp.status_code == 200
            diagnostics = resp.json()["data"]
            assert diagnostics["status"] == "partial"
            assert [entry["name"] for entry in diagnostics["rejected"]] == [
                "stale-provider"
            ]

            # The neighbour still serves: that is the whole claim.
            list_resp = await client.get("/api/v1/providers")
            assert list_resp.status_code == 200
            served = {p["name"] for p in list_resp.json()["data"]}
            assert "serving-provider" in served
            assert "stale-provider" not in served

    async def test_unreadable_config_is_not_an_empty_one(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        app = await _build_app_with_db_providers(
            fake_persistence,
            fake_message_bus,
            {
                "only-provider": {
                    "driver": "litellm",
                    "connection_name": "provider-only",
                    "retired_key": "value",
                },
            },
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/providers/config-diagnostics")
            assert resp.status_code == 200
            diagnostics = resp.json()["data"]
            # An empty provider list reads the same either way, so this is
            # the only surface that separates "nothing configured" from
            # "nothing readable".
            assert diagnostics["status"] == "unreadable"
            assert [entry["name"] for entry in diagnostics["rejected"]] == [
                "only-provider"
            ]

    async def test_readable_config_reports_ok(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        app = await _build_app_with_db_providers(
            fake_persistence,
            fake_message_bus,
            {
                "test-provider": {
                    "driver": "litellm",
                    "connection_name": "provider-test",
                },
            },
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/providers/config-diagnostics")
            assert resp.status_code == 200
            diagnostics = resp.json()["data"]
            assert diagnostics["status"] == "ok"
            assert diagnostics["rejected"] == []
            assert diagnostics["detail"] is None
