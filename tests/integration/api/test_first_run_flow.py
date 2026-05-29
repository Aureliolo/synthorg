"""Integration test for the full first-run setup flow.

Exercises the complete wizard path: status check, admin creation,
login, template listing, company creation, agent creation, and
setup completion -- verifying that agents end up in the runtime
registry.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar import Litestar

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests._shared import build_test_app as create_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="
_TEST_USERNAME = "admin"
_TEST_PASSWORD = "secure-pass-12chars"


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for the API backend."""
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


def _build_first_run_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Litestar:
    """Build a full app with no pre-seeded users (fresh first-run state)."""
    config = RootConfig(company_name="test-company")
    auth_service = AuthService(
        config.api.auth.model_copy(update={"jwt_secret": _TEST_JWT_SECRET}),
    )
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    # A provider registry with a test provider so the completion step can
    # verify providers are configured.
    stub = MagicMock(spec=BaseCompletionProvider)
    return create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        agent_registry=AgentRegistryService(),
        settings_service=settings_service,
        provider_registry=ProviderRegistry({"test-provider": stub}),
    )


def _wire_mock_provider_management(app: Litestar) -> None:
    """Wire mock provider management so agent creation can validate models.

    Wired AFTER ``create_app`` so the setup-agent controller's
    ``provider_management_of(app_state).list_providers()`` returns our stub
    mapping (a real provider + model pair to validate against).
    """
    from synthorg.core.agent import ModelConfig
    from synthorg.providers.state import ProvidersStateSlice

    mock_model = MagicMock(spec=ModelConfig)
    mock_model.id = "test-small-001"
    mock_model.alias = None
    mock_provider_config = MagicMock(spec=ProviderConfig)
    mock_provider_config.models = [mock_model]

    mock_mgmt = MagicMock(spec=ProviderManagementService)
    mock_mgmt.list_providers = AsyncMock(
        return_value={"test-provider": mock_provider_config},
    )
    app.state["app_state"].wire(ProvidersStateSlice, management=mock_mgmt)


@pytest.fixture
async def integration_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> AsyncGenerator[LoopAsyncClient]:
    """Yield a portal-free client over a fresh first-run app."""
    app = _build_first_run_app(fake_persistence, fake_message_bus)
    _wire_mock_provider_management(app)
    async with LoopAsyncClient(app) as client:
        yield client


def _extract_auth_cookies(resp: Any) -> tuple[str, str]:
    """Extract session token and CSRF token from Set-Cookie headers."""
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


async def _bootstrap_admin_and_login(client: LoopAsyncClient) -> None:
    """Run wizard steps 1-3: status check, admin creation, login.

    Sets the session + CSRF cookie headers on ``client`` for the
    authenticated requests that follow.
    """
    # 1. GET /setup/status -- needs_admin should be True
    resp = await client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    status_data = resp.json()["data"]
    assert status_data["needs_admin"] is True
    assert status_data["needs_setup"] is True

    # 2. POST /auth/setup -- create admin account
    resp = await client.post(
        "/api/v1/auth/setup",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["expires_in"] > 0
    session_token, csrf_token = _extract_auth_cookies(resp)
    assert session_token, "missing session cookie after setup"
    assert csrf_token, "missing CSRF cookie after setup"

    # 3. POST /auth/login -- verify credentials work
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
        headers={
            "Cookie": f"session={session_token}; csrf_token={csrf_token}",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["expires_in"] > 0
    jwt_token, csrf_token = _extract_auth_cookies(resp)
    assert jwt_token, "login did not issue session cookie"
    assert csrf_token, "login did not issue CSRF cookie"

    # Use cookie + CSRF for all subsequent requests.
    client.headers["Cookie"] = f"session={jwt_token}; csrf_token={csrf_token}"
    client.headers["X-CSRF-Token"] = csrf_token


@pytest.mark.integration
class TestFirstRunFlow:
    """Full first-run setup wizard integration test."""

    async def test_full_first_run_flow(
        self,
        integration_client: LoopAsyncClient,
    ) -> None:
        """Exercise the entire first-run wizard from status to completion."""
        client = integration_client
        await _bootstrap_admin_and_login(client)

        # ── 4. GET /setup/templates -- list available templates ──
        resp = await client.get("/api/v1/setup/templates")
        assert resp.status_code == 200
        templates = resp.json()["data"]
        assert len(templates) >= 1
        # Verify at least one known template exists.
        template_names = {t["name"] for t in templates}
        assert "solo_founder" in template_names

        # ── 5. POST /setup/company -- create a company ──
        resp = await client.post(
            "/api/v1/setup/company",
            json={"company_name": "Integration Test Corp"},
        )
        assert resp.status_code == 201
        company_data = resp.json()["data"]
        assert company_data["company_name"] == "Integration Test Corp"

        # ── 6. POST /setup/agent -- create an agent ──
        resp = await client.post(
            "/api/v1/setup/agent",
            json={
                "name": "test-agent-ceo",
                "role": "CEO",
                "model_provider": "test-provider",
                "model_id": "test-small-001",
            },
        )
        assert resp.status_code == 201
        agent_data = resp.json()["data"]
        assert agent_data["name"] == "test-agent-ceo"
        assert agent_data["role"] == "CEO"

        # ── 7. POST /setup/complete -- mark setup as done ──
        resp = await client.post("/api/v1/setup/complete")
        assert resp.status_code == 201
        complete_data = resp.json()["data"]
        assert complete_data["setup_complete"] is True

        # ── 8. Verify agents are registered in the runtime registry ──
        from synthorg.hr.state import HrStateSlice

        app_state = client.app.state.app_state
        agent_count = await app_state.slice(HrStateSlice).agent_registry.agent_count()
        assert agent_count >= 1
