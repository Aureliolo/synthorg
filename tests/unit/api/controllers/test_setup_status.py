"""Tests for the setup status endpoint (GET /api/v1/setup/status)."""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from litestar.testing import TestClient

from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.roles import HumanRole
from synthorg.persistence.state import persistence_of
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice

_DEFAULT_MIN_PW = AuthConfig.model_fields["min_password_length"].default


@pytest.mark.unit
class TestSetupStatus:
    """GET /api/v1/setup/status -- unauthenticated status check."""

    def test_returns_status_with_seeded_users(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """With pre-seeded users, needs_admin is False."""
        app_state = test_client.app.state.app_state
        original_registry = app_state.slice(ProvidersStateSlice).registry
        app_state.wire(ProvidersStateSlice, registry=ProviderRegistry({}))
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            data = body["data"]
            assert data["needs_admin"] is False
            assert data["needs_setup"] is True
            assert data["has_providers"] is False
        finally:
            app_state.wire(ProvidersStateSlice, registry=original_registry)

    def test_status_without_auth_header(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Status endpoint works without authentication."""
        saved_headers = dict(test_client.headers)
        test_client.headers.pop("Authorization", None)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
        finally:
            test_client.headers.update(saved_headers)

    def test_status_response_fields(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Status response contains all required fields."""
        resp = test_client.get("/api/v1/setup/status")
        data = resp.json()["data"]
        assert "needs_admin" in data
        assert "needs_setup" in data
        assert "has_providers" in data
        assert isinstance(data["needs_admin"], bool)
        assert isinstance(data["needs_setup"], bool)
        assert isinstance(data["has_providers"], bool)

    def test_needs_admin_true_when_only_non_admin_exists(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """needs_admin is True when only non-CEO users exist."""
        app_state = test_client.app.state.app_state
        users_repo = cast(Any, persistence_of(app_state))._users

        # Remove all CEO users, keep only observers
        removed = {
            uid: users_repo._users.pop(uid)
            for uid in [
                uid for uid, u in users_repo._users.items() if u.role == HumanRole.CEO
            ]
        }
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["needs_admin"] is True
        finally:
            users_repo._users.update(removed)

    def test_needs_admin_false_when_ceo_exists(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """needs_admin is False when a CEO user exists (default fixture)."""
        resp = test_client.get("/api/v1/setup/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["needs_admin"] is False

    def test_status_includes_min_password_length(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Status includes min_password_length (falls back to default)."""
        resp = test_client.get("/api/v1/setup/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "min_password_length" in data
        assert isinstance(data["min_password_length"], int)
        assert data["min_password_length"] == _DEFAULT_MIN_PW

    def test_status_returns_configured_min_password_length(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Status returns non-default min_password_length from settings."""
        app_state = test_client.app.state.app_state
        settings_repo = cast(Any, persistence_of(app_state))._settings_repo
        now = datetime.now(UTC).isoformat()

        settings_repo._store[("api", "min_password_length")] = ("16", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["min_password_length"] == 16
        finally:
            settings_repo._store.pop(("api", "min_password_length"), None)

    def test_has_agents_false_for_non_list_json(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_agents is False when agents setting contains non-list JSON."""
        app_state = test_client.app.state.app_state
        settings_repo = cast(Any, persistence_of(app_state))._settings_repo
        now = datetime.now(UTC).isoformat()

        agents_key = ("company", "agents")
        original = settings_repo._store.get(agents_key)
        settings_repo._store[agents_key] = ("42", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_agents"] is False
        finally:
            if original is None:
                settings_repo._store.pop(agents_key, None)
            else:
                settings_repo._store[agents_key] = original

    def test_has_agents_false_for_invalid_json(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_agents is False when agents setting contains invalid JSON."""
        app_state = test_client.app.state.app_state
        settings_repo = cast(Any, persistence_of(app_state))._settings_repo
        now = datetime.now(UTC).isoformat()

        agents_key = ("company", "agents")
        original = settings_repo._store.get(agents_key)
        settings_repo._store[agents_key] = ("{not valid json", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_agents"] is False
        finally:
            if original is None:
                settings_repo._store.pop(agents_key, None)
            else:
                settings_repo._store[agents_key] = original

    def test_min_password_length_non_integer_returns_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Non-integer min_password_length falls back to the default (12)."""
        app_state = test_client.app.state.app_state
        settings_repo = cast(Any, persistence_of(app_state))._settings_repo
        now = datetime.now(UTC).isoformat()

        pw_key = ("api", "min_password_length")
        original = settings_repo._store.get(pw_key)
        settings_repo._store[pw_key] = ("abc", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["min_password_length"] == _DEFAULT_MIN_PW
        finally:
            if original is None:
                settings_repo._store.pop(pw_key, None)
            else:
                settings_repo._store[pw_key] = original

    def test_min_password_length_below_default_returns_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """min_password_length below default (12) is clamped to default."""
        app_state = test_client.app.state.app_state
        settings_repo = cast(Any, persistence_of(app_state))._settings_repo
        now = datetime.now(UTC).isoformat()

        pw_key = ("api", "min_password_length")
        original = settings_repo._store.get(pw_key)
        settings_repo._store[pw_key] = ("5", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["min_password_length"] == _DEFAULT_MIN_PW
        finally:
            if original is None:
                settings_repo._store.pop(pw_key, None)
            else:
                settings_repo._store[pw_key] = original

    # ── DATABASE source guard tests ──────────────────────────

    def test_has_company_false_when_only_yaml_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_company is False when company_name comes from YAML defaults.

        The fixture's root_config provides company_name="test-company"
        via YAML.  With no DB-stored value, the status endpoint must
        report has_company=False.
        """
        repo = cast(Any, persistence_of(test_client.app.state.app_state))._settings_repo
        key = ("company", "company_name")
        original = repo._store.get(key)
        repo._store.pop(key, None)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_company"] is False
        finally:
            if original is not None:
                repo._store[key] = original

    def test_has_company_true_when_db_persisted(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_company is True when company_name is DB-persisted."""
        repo = cast(Any, persistence_of(test_client.app.state.app_state))._settings_repo
        now = datetime.now(UTC).isoformat()
        key = ("company", "company_name")
        original = repo._store.get(key)
        repo._store[key] = ("Test Corp", now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_company"] is True
        finally:
            if original is None:
                repo._store.pop(key, None)
            else:
                repo._store[key] = original

    def test_has_agents_false_when_only_yaml_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_agents is False when agents come from a non-DB source.

        Simulates a non-DB source by removing the DB entry so the
        settings service falls through to YAML/code defaults.
        """
        repo = cast(Any, persistence_of(test_client.app.state.app_state))._settings_repo
        agents_key = ("company", "agents")
        original = repo._store.get(agents_key)
        repo._store.pop(agents_key, None)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_agents"] is False
        finally:
            if original is not None:
                repo._store[agents_key] = original

    def test_has_agents_true_when_db_persisted(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """has_agents is True when agents list is DB-persisted."""
        repo = cast(Any, persistence_of(test_client.app.state.app_state))._settings_repo
        now = datetime.now(UTC).isoformat()
        agents_key = ("company", "agents")
        original = repo._store.get(agents_key)
        agents = json.dumps([{"name": "agent-001", "role": "CEO"}])
        repo._store[agents_key] = (agents, now)
        try:
            resp = test_client.get("/api/v1/setup/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_agents"] is True
        finally:
            if original is None:
                repo._store.pop(agents_key, None)
            else:
                repo._store[agents_key] = original
