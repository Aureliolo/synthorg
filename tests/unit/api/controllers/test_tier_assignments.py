"""Tests for the model tier-assignment REST endpoints."""

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig, RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_BASE = "/api/v1/providers/tier-assignments"
_CEO = make_auth_headers("ceo")
_OBSERVER = make_auth_headers("observer")


def _build_client(
    *,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> LoopAsyncClient:
    """Build a client with one provider (test-provider / test-small-001)."""
    from synthorg.api.auth.service import AuthService
    from synthorg.budget.tracker import CostTracker
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

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
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
    )
    return LoopAsyncClient(app)


@pytest.mark.unit
class TestTierAssignmentsApi:
    async def test_list_returns_heuristic_assignment(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
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
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
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
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
        resp = await client.put(
            f"{_BASE}/test-provider/ghost-model",
            json={"tier": "large"},
            headers=_CEO,
        )
        assert resp.status_code == 404

    async def test_override_unknown_provider_is_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
        resp = await client.put(
            f"{_BASE}/ghost-provider/test-small-001",
            json={"tier": "large"},
            headers=_CEO,
        )
        assert resp.status_code == 404

    async def test_recommend_without_classifier_is_409(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
        resp = await client.post(
            f"{_BASE}/test-provider/test-small-001/recommend", headers=_CEO
        )
        # Disabled by default: the opt-in gate fails closed before the model check.
        assert resp.status_code == 409

    async def test_classifier_model_round_trips_enabled(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
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
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        client = _build_client(
            fake_persistence=fake_persistence, fake_message_bus=fake_message_bus
        )
        resp = await client.put(
            f"{_BASE}/test-provider/test-small-001",
            json={"tier": "large"},
            headers=_OBSERVER,
        )
        assert resp.status_code == 403
