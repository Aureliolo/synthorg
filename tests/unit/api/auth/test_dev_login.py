"""Tests for the DEV-ONLY password-free /auth/dev-login endpoint + its gating.

The endpoint mints a real admin session with no password, so the security-
critical behaviour is the gate: it must be inert (404, and not auth-excluded)
unless ``SYNTHORG_DEV_AUTH_BYPASS`` was set at startup.
"""

from collections.abc import Sequence
from typing import cast

import pytest

from synthorg.api.api_core_state import auth_service_of
from synthorg.api.auth.secret import resolve_dev_auth_bypass
from synthorg.api.middleware_factory import _build_auth_exclude_paths
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import User
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient
from tests.unit.api.fakes import FakePersistenceBackend

_DEV_LOGIN_PATTERN = "^/api/v1/auth/dev-login$"


def _enable_dev_bypass(
    client: LoopAsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flip the live auth service's config to dev_auth_bypass=True."""
    app_state = client.app.state["app_state"]
    svc = auth_service_of(app_state)
    monkeypatch.setattr(
        svc,
        "_config",
        svc._config.model_copy(update={"dev_auth_bypass": True}),
    )


@pytest.mark.unit
class TestResolveDevAuthBypass:
    def test_unset_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYNTHORG_DEV_AUTH_BYPASS", raising=False)
        assert resolve_dev_auth_bypass() is False

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "ON"])
    def test_truthy_values_enable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("SYNTHORG_DEV_AUTH_BYPASS", value)
        assert resolve_dev_auth_bypass() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "  "])
    def test_falsy_values_stay_disabled(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("SYNTHORG_DEV_AUTH_BYPASS", value)
        assert resolve_dev_auth_bypass() is False


@pytest.mark.unit
class TestDevLoginExcludePathGating:
    def test_excluded_from_auth_only_when_enabled(self) -> None:
        auth = AuthConfig()
        off = _build_auth_exclude_paths(
            auth, "/api/v1", "/api/v1/ws/connect", dev_auth_bypass=False
        )
        on = _build_auth_exclude_paths(
            auth, "/api/v1", "/api/v1/ws/connect", dev_auth_bypass=True
        )
        assert _DEV_LOGIN_PATTERN not in off
        assert _DEV_LOGIN_PATTERN in on


@pytest.mark.unit
class TestDevLoginEndpoint:
    async def test_disabled_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Default config has dev_auth_bypass=False, so even an authenticated
        # caller is treated as if the route does not exist.
        response = await async_test_client.post("/api/v1/auth/dev-login")
        assert response.status_code == 404

    async def test_enabled_mints_admin_session(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A CEO (test-ceo) is pre-seeded by the fixture.
        _enable_dev_bypass(async_test_client, monkeypatch)
        response = await async_test_client.post("/api/v1/auth/dev-login")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "token" not in data  # JWT lives in the HttpOnly cookie
        assert data["expires_in"] > 0
        assert "session=" in response.headers.get("set-cookie", "")

    async def test_enabled_without_admin_returns_401(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_dev_bypass(async_test_client, monkeypatch)
        app_state = async_test_client.app.state["app_state"]
        users = cast(FakePersistenceBackend, persistence_of(app_state))._users

        async def _no_admins(*_args: object, **_kwargs: object) -> Sequence[User]:
            return ()

        # Keep the authed user resolvable (middleware uses ``get``), but make the
        # handler's admin lookup come back empty so it hits the no-admin branch.
        monkeypatch.setattr(users, "query", _no_admins)
        response = await async_test_client.post("/api/v1/auth/dev-login")
        assert response.status_code == 401
