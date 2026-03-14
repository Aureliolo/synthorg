"""Tests for agent controller."""

from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.config.schema import AgentConfig, RootConfig
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


@pytest.mark.unit
class TestAgentController:
    def test_list_agents_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    def test_list_agents_with_data(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.app import create_app
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(
            company_name="test",
            agents=(
                AgentConfig(
                    name="alice",
                    role="developer",
                    department="eng",
                ),
            ),
        )
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        app = create_app(
            config=config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=CostTracker(),
            auth_service=auth_service,
        )
        with TestClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = client.get("/api/v1/agents")
            body = resp.json()
            assert body["pagination"]["total"] == 1
            assert body["data"][0]["name"] == "alice"

    def test_get_agent_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/agents/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False
