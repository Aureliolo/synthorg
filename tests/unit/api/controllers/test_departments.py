"""Tests for department controller."""

import json

import pytest

from synthorg.config.schema import RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


@pytest.mark.unit
class TestDepartmentController:
    async def test_list_departments_empty(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/departments")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_get_department_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/departments/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_oversized_department_name_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_name = "x" * 129
        resp = await async_test_client.get(f"/api/v1/departments/{long_name}")
        assert resp.status_code == 400


@pytest.mark.unit
class TestDepartmentControllerDbOverride:
    """Test that DB-stored settings override YAML departments."""

    async def test_db_departments_override_config(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests._shared import build_test_app as create_app
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(company_name="test")
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )

        db_depts = [
            {"name": "db-dept", "head": "alice"},
        ]
        await settings_service.set("company", "departments", json.dumps(db_depts))

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
            resp = await client.get("/api/v1/departments")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"][0]["name"] == "db-dept"

            detail_resp = await client.get("/api/v1/departments/db-dept")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["data"]["name"] == "db-dept"
