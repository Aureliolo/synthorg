"""Empty-company task submission rejection (provider-present switch).

With no LLM provider configured the company is empty: ``POST /tasks``
must be rejected with a clear 409 message instead of creating a task
that can never execute. With a provider present, creation succeeds.
"""

from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="
_USERNAME = "admin"
_PASSWORD = "secure-pass-12chars"


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


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


def _build_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    *,
    with_provider: bool,
) -> Any:
    root_config = RootConfig(company_name="empty-company-test")
    auth_service = AuthService(
        root_config.api.auth.model_copy(update={"jwt_secret": _TEST_JWT_SECRET}),
    )
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    provider_registry = (
        ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
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
    )


def _extract_auth_cookies(resp: Any) -> tuple[str, str]:
    session = ""
    csrf = ""
    for k, v in resp.headers.multi_items():
        if k != "set-cookie":
            continue
        if v.startswith("session="):
            session = v.split("session=")[1].split(";")[0]
        elif v.startswith("csrf_token="):
            csrf = v.split("csrf_token=")[1].split(";")[0]
    return session, csrf


def _authed(app: Any) -> Generator[TestClient[Any]]:
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/setup",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        session_token, csrf_token = _extract_auth_cookies(resp)
        client.headers["Cookie"] = f"session={session_token}; csrf_token={csrf_token}"
        client.headers["X-CSRF-Token"] = csrf_token
        yield client


@pytest.fixture
def empty_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Generator[TestClient[Any]]:
    yield from _authed(
        _build_app(fake_persistence, fake_message_bus, with_provider=False)
    )


@pytest.fixture
def provider_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Generator[TestClient[Any]]:
    yield from _authed(
        _build_app(fake_persistence, fake_message_bus, with_provider=True)
    )


def _task_payload() -> dict[str, Any]:
    return {
        "title": "Build the thing",
        "description": "A task that needs an agent to run.",
        "type": "development",
        "project": "proj-1",
        "created_by": _USERNAME,
    }


class TestEmptyCompanyRejectsTaskCreation:
    def test_no_provider_rejects_with_clear_message(
        self,
        empty_company_client: TestClient[Any],
    ) -> None:
        resp = empty_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert "provider" in resp.text.lower()
        assert "empty mode" in resp.text.lower()
        assert body["error_detail"]["error_code"] == 4014

    def test_provider_present_allows_creation(
        self,
        provider_company_client: TestClient[Any],
    ) -> None:
        resp = provider_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 201, resp.text
