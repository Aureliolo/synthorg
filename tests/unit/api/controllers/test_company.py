"""Tests for company controller."""

import json
from unittest.mock import AsyncMock

import pytest
from litestar import Litestar

from synthorg.config.schema import RootConfig
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_HEADERS = make_auth_headers("ceo")


@pytest.fixture
async def db_override_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> tuple[Litestar, SettingsService]:
    """Build an app with a real SettingsService for DB-override tests."""
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
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
    )
    return app, settings_service


@pytest.mark.unit
class TestCompanyController:
    async def test_get_company(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/company", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["company_name"] == "test-company"

    async def test_list_departments(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/company/departments", headers=_HEADERS
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_company_requires_read_access(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/company",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.integration
class TestCompanyControllerDbOverride:
    """Test that DB-stored settings override YAML company data."""

    async def test_db_company_departments_override(
        self,
        db_override_app: tuple[Litestar, SettingsService],
    ) -> None:
        app, settings_service = db_override_app
        db_depts = [{"name": "db-sales", "head": "bob"}]
        await settings_service.set("company", "departments", json.dumps(db_depts))

        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = await client.get("/api/v1/company/departments")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert len(body["data"]) == 1
            assert body["data"][0]["name"] == "db-sales"

    async def test_taskgroup_error_returns_clean_error_response(
        self,
        db_override_app: tuple[Litestar, SettingsService],
    ) -> None:
        """Verify TaskGroup exception unwraps to a clean API error."""
        app, _settings_service = db_override_app
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            from synthorg.settings.state import SettingsStateSlice

            resolver = app.state.app_state.slice(SettingsStateSlice).config_resolver
            original_get_str = resolver.get_str
            resolver.get_str = AsyncMock(
                spec=original_get_str,
                side_effect=SettingNotFoundError("company/company_name"),
            )
            resp = await client.get("/api/v1/company")
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert body["error"] is not None

    async def test_db_company_overview_includes_db_agents(
        self,
        db_override_app: tuple[Litestar, SettingsService],
    ) -> None:
        app, settings_service = db_override_app
        db_agents = [{"name": "db-agent", "role": "dev", "department": "eng"}]
        await settings_service.set("company", "agents", json.dumps(db_agents))

        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = await client.get("/api/v1/company")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert len(body["data"]["agents"]) == 1
            assert body["data"]["agents"][0]["name"] == "db-agent"
