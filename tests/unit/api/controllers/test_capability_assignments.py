"""Tests for the model tier-assignment REST endpoints."""

from collections.abc import AsyncIterator

import pytest
from litestar import Litestar

from synthorg.api.auth.service import AuthService
from synthorg.config.schema import ProviderConfig, ProviderModelConfig, RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    _seed_test_users,
    make_auth_headers,
)

_BASE = "/api/v1/providers/capability-assignments"
_CEO = make_auth_headers("ceo")
_OBSERVER = make_auth_headers("observer")


@pytest.fixture(scope="class")
def tier_settings(fake_persistence: FakePersistenceBackend) -> SettingsService:
    """The settings service the shared app is built on."""
    return SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )


@pytest.fixture(scope="class")
def tier_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    auth_service: AuthService,
    tier_settings: SettingsService,
) -> Litestar:
    """One provider (test-provider / test-small-001), built once per class.

    Assembling the app dominates this file's runtime and every case wants the
    same one-provider company, so it is built once and ``_reset_tier_state``
    restores the mutable state each case depends on.
    """
    from synthorg.budget.tracker import CostTracker
    from tests._shared import build_test_app as create_app

    config = RootConfig(
        company_name="test",
        providers={
            "test-provider": ProviderConfig(
                connection_name="conn-test",
                driver="litellm",
                models=(ProviderModelConfig(id="test-small-001", alias="small"),),
            ),
        },
    )
    return create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=tier_settings,
        # The persistence and bus are session-scoped and shared with every
        # other API test, so this app must not disconnect them on teardown.
        _skip_lifecycle_shutdown=True,
    )


@pytest.fixture(autouse=True)
def _reset_tier_state(
    fake_persistence: FakePersistenceBackend,
    auth_service: AuthService,
    tier_settings: SettingsService,
) -> None:
    """Undo tier overrides / classifier writes the shared app persisted."""
    fake_persistence.clear()
    _seed_test_users(fake_persistence, auth_service)
    tier_settings._cache.clear()


@pytest.fixture
async def client(tier_app: Litestar) -> AsyncIterator[LoopAsyncClient]:
    """A fresh transport per test, bound to the shared app.

    Entered as a context manager so the ASGI lifespan runs on this test's loop
    and the transport is closed afterwards.
    """
    async with LoopAsyncClient(tier_app) as entered:
        yield entered


@pytest.mark.unit
class TestCapabilityAssignmentsApi:
    async def test_list_returns_heuristic_assignment(
        self,
        client: LoopAsyncClient,
    ) -> None:
        resp = await client.get(_BASE, headers=_CEO)
        assert resp.status_code == 200
        assignments = resp.json()["data"]["assignments"]
        assert len(assignments) == 1
        row = assignments[0]
        assert row["provider"] == "test-provider"
        assert row["model_id"] == "test-small-001"
        assert row["provenance"] == "heuristic"
        assert row["is_override"] is False

    async def test_override_then_clear_round_trips(
        self,
        client: LoopAsyncClient,
    ) -> None:
        put = await client.put(
            f"{_BASE}/test-provider/test-small-001",
            json={"tier": "large", "reason": "manual"},
            headers=_CEO,
        )
        assert put.status_code == 200
        row = put.json()["data"]["assignments"][0]
        assert row["tier"] == "large"
        assert row["provenance"] == "operator"
        assert row["is_override"] is True

        clear = await client.put(
            f"{_BASE}/test-provider/test-small-001",
            json={"tier": None},
            headers=_CEO,
        )
        assert clear.status_code == 200
        assert clear.json()["data"]["assignments"][0]["is_override"] is False

    async def test_override_unknown_model_is_404(
        self,
        client: LoopAsyncClient,
    ) -> None:
        resp = await client.put(
            f"{_BASE}/test-provider/ghost-model",
            json={"tier": "large"},
            headers=_CEO,
        )
        assert resp.status_code == 404

    async def test_override_unknown_provider_is_404(
        self,
        client: LoopAsyncClient,
    ) -> None:
        resp = await client.put(
            f"{_BASE}/ghost-provider/test-small-001",
            json={"tier": "large"},
            headers=_CEO,
        )
        assert resp.status_code == 404

    async def test_recommend_without_classifier_is_409(
        self,
        client: LoopAsyncClient,
    ) -> None:
        resp = await client.post(
            f"{_BASE}/test-provider/test-small-001/recommend", headers=_CEO
        )
        # Disabled by default: the opt-in gate fails closed before the model check.
        assert resp.status_code == 409

    async def test_classifier_model_round_trips_enabled(
        self,
        client: LoopAsyncClient,
    ) -> None:
        get = await client.get(f"{_BASE}/classifier-model", headers=_CEO)
        assert get.status_code == 200
        assert get.json()["data"]["enabled"] is False

        put = await client.put(
            f"{_BASE}/classifier-model",
            json={
                "provider": "test-provider",
                "model_id": "test-small-001",
                "enabled": True,
            },
            headers=_CEO,
        )
        assert put.status_code == 200
        stored = put.json()["data"]
        assert stored["provider"] == "test-provider"
        assert stored["enabled"] is True

        again = await client.get(f"{_BASE}/classifier-model", headers=_CEO)
        assert again.json()["data"]["enabled"] is True

    async def test_write_requires_manager_role(
        self,
        client: LoopAsyncClient,
    ) -> None:
        resp = await client.put(
            f"{_BASE}/test-provider/test-small-001",
            json={"tier": "large"},
            headers=_OBSERVER,
        )
        assert resp.status_code == 403
