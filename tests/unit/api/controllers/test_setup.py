"""Tests for the first-run setup controller.

Covers template listing, company creation, setup completion,
and the template department extraction helpers.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from litestar.testing import TestClient

from synthorg.api.controllers.setup_agents import normalize_description
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestSetupTemplates:
    """GET /api/v1/setup/templates -- list available templates."""

    def test_returns_builtin_templates(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/templates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        templates = body["data"]
        assert len(templates) >= 7
        names = {t["name"] for t in templates}
        assert "solo_founder" in names
        assert "startup" in names

    def test_template_fields(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/templates")
        body = resp.json()
        for template in body["data"]:
            assert "name" in template
            assert "display_name" in template
            assert "description" in template
            assert "source" in template
            assert "tags" in template
            assert isinstance(template["tags"], list)
            assert "skill_patterns" in template
            assert isinstance(template["skill_patterns"], list)
            assert "variables" in template
            assert isinstance(template["variables"], list)
            assert "agent_count" in template
            assert isinstance(template["agent_count"], int)
            assert template["agent_count"] >= 0
            assert "department_count" in template
            assert isinstance(template["department_count"], int)
            assert template["department_count"] >= 0
            assert "autonomy_level" in template
            assert template["autonomy_level"] in (
                "full",
                "semi",
                "supervised",
                "locked",
            )
            assert "workflow" in template
            assert isinstance(template["workflow"], str)

    def test_observer_can_read_templates(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Observer role has read access to templates."""
        saved_headers = dict(test_client.headers)
        test_client.headers.update(make_auth_headers("observer"))
        try:
            resp = test_client.get("/api/v1/setup/templates")
            assert resp.status_code == 200
        finally:
            test_client.headers.update(saved_headers)


@pytest.mark.unit
class TestSetupCompany:
    """POST /api/v1/setup/company -- create company config."""

    def test_blank_company(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "Test Corp"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["company_name"] == "Test Corp"
        assert data["template_applied"] is None
        assert data["department_count"] == 0
        assert data["description"] is None

        # Verify description persisted as "" (absent convention).
        app_state = test_client.app.state.app_state
        settings_repo = app_state.persistence._settings_repo
        stored = settings_repo._store.get(("company", "description"))
        assert stored is not None
        assert stored[0] == ""

    @pytest.mark.parametrize(
        ("description_input", "expected_response", "expected_stored"),
        [
            (
                "An AI-powered test organization",
                "An AI-powered test organization",
                "An AI-powered test organization",
            ),
            ("  hello world  ", "hello world", "hello world"),
            ("   ", None, ""),
            ("", None, ""),
        ],
        ids=["normal", "stripped", "whitespace-only", "empty"],
    )
    def test_description_normalization(
        self,
        test_client: TestClient[Any],
        description_input: str,
        expected_response: str | None,
        expected_stored: str,
    ) -> None:
        """Description is stripped and blank values normalized to None."""
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "Test Corp",
                "description": description_input,
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["description"] == expected_response

        # Verify persistence.
        app_state = test_client.app.state.app_state
        settings_repo = app_state.persistence._settings_repo
        stored = settings_repo._store.get(("company", "description"))
        assert stored is not None
        assert stored[0] == expected_stored

    @given(description=st.text(max_size=1000))
    def test_description_normalization_invariants(
        self,
        description: str,
    ) -> None:
        """Normalization invariant: blank -> None, non-blank -> stripped.

        Tests the pure normalization function directly -- the HTTP
        round-trip is already covered by test_description_normalization
        with explicit parametrized cases.
        """
        result = normalize_description(description)
        stripped = description.strip()
        if stripped == "":
            assert result is None
        else:
            assert result == stripped

    def test_company_description_too_long(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Description exceeding 1000 characters is rejected."""
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "Test Corp",
                "description": "x" * 1001,
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error_detail"]["error_category"] == "validation"

    def test_description_at_max_length_accepted(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Description of exactly 1000 characters is accepted."""
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "Test Corp",
                "description": "x" * 1000,
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["description"] == "x" * 1000

        # Verify persistence.
        app_state = test_client.app.state.app_state
        settings_repo = app_state.persistence._settings_repo
        stored = settings_repo._store.get(("company", "description"))
        assert stored is not None
        assert stored[0] == "x" * 1000

    def test_description_overwrite_clears_stale(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Re-creating company without description clears previous value."""
        # First create with a description.
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "Test Corp",
                "description": "Original description",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["description"] == "Original description"

        # Re-create without description -- stale value must be cleared.
        resp = test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "Test Corp v2"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["description"] is None

        # Verify persistence cleared.
        app_state = test_client.app.state.app_state
        settings_repo = app_state.persistence._settings_repo
        stored = settings_repo._store.get(("company", "description"))
        assert stored is not None
        assert stored[0] == ""

    def test_invalid_template(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "Test Corp",
                "template_name": "nonexistent_template",
            },
        )
        assert resp.status_code == 404

    def test_blank_company_name_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "   "},
        )
        # Pydantic NotBlankStr validation returns 400
        assert resp.status_code == 400

    def test_requires_write_access(
        self,
        test_client: TestClient[Any],
    ) -> None:
        saved_headers = dict(test_client.headers)
        test_client.headers.update(make_auth_headers("observer"))
        try:
            resp = test_client.post(
                "/api/v1/setup/company",
                json={"company_name": "Test Corp"},
            )
            assert resp.status_code == 403
        finally:
            test_client.headers.update(saved_headers)


@pytest.mark.unit
class TestSetupComplete:
    """POST /api/v1/setup/complete -- mark setup as done."""

    def test_requires_write_access(
        self,
        test_client: TestClient[Any],
    ) -> None:
        saved_headers = dict(test_client.headers)
        test_client.headers.update(make_auth_headers("observer"))
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 403
        finally:
            test_client.headers.update(saved_headers)

    def test_complete_rejects_without_company(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Completion rejects when no company name is set."""
        app_state = test_client.app.state.app_state
        settings_repo = app_state.persistence._settings_repo

        # Remove company_name from the settings store so the YAML
        # fallback chain also yields nothing.  The fixture's root_config
        # provides company_name, so we need to override at the DB level
        # with an empty string to simulate "not configured".
        now = datetime.now(UTC).isoformat()
        settings_repo._store[("company", "company_name")] = ("", now)
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 422
            assert "company" in resp.json()["error"].lower()
        finally:
            settings_repo._store.pop(("company", "company_name"), None)

    def test_complete_rejects_without_db_company(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Completion rejects when company_name is only in YAML defaults."""
        repo = test_client.app.state.app_state.persistence._settings_repo
        key = ("company", "company_name")
        original = repo._store.get(key)
        repo._store.pop(key, None)
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 422
            assert "company" in resp.json()["error"].lower()
        finally:
            if original is not None:
                repo._store[key] = original

    def test_complete_allows_without_agents(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Completion succeeds without agents (Quick Setup mode)."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        # Ensure at least one provider is registered.
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 201
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["setup_complete"] is True
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)

    def test_complete_rejects_without_providers(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Completion rejects when company and agents exist but no providers."""
        repo = test_client.app.state.app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps([{"name": "agent-001", "role": "CEO"}])
        repo._store[("company", "agents")] = (agents, now)
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 422
            assert "provider" in resp.json()["error"].lower()
        finally:
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)

    def test_complete_succeeds_with_all_prerequisites(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Completion succeeds when company, agents, and providers exist."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps([{"name": "agent-001", "role": "CEO"}])
        repo._store[("company", "agents")] = (agents, now)
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 201
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["setup_complete"] is True
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)

    def test_complete_bootstraps_agents_into_registry(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Setup completion registers agents in the runtime registry."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps(
            [
                {
                    "name": "alice",
                    "role": "developer",
                    "department": "engineering",
                    "model": {
                        "provider": "test-provider",
                        "model_id": "test-small-001",
                    },
                },
            ]
        )
        repo._store[("company", "agents")] = (agents, now)
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )
        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 201
            assert resp.json()["data"]["setup_complete"] is True
            # Agent should now be in the runtime registry.
            loop = asyncio.new_event_loop()
            try:
                agent_count = loop.run_until_complete(
                    app_state.agent_registry.agent_count(),
                )
            finally:
                loop.close()
            assert agent_count >= 1
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)

    def test_complete_succeeds_even_if_bootstrap_fails(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setup completion returns 201 even if agent bootstrap raises."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps([{"name": "agent-001", "role": "CEO"}])
        repo._store[("company", "agents")] = (agents, now)
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )

        # Make bootstrap_agents raise to simulate failure.
        failing_bootstrap = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            "synthorg.api.bootstrap.bootstrap_agents",
            failing_bootstrap,
        )

        try:
            resp = test_client.post("/api/v1/setup/complete")
            # Must succeed despite bootstrap failure (non-fatal).
            assert resp.status_code == 201
            assert resp.json()["data"]["setup_complete"] is True
            failing_bootstrap.assert_awaited_once()
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)

    def test_complete_reloads_provider_registry(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setup completion reloads the provider registry from config."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps(
            [
                {
                    "name": "alice",
                    "role": "developer",
                    "department": "engineering",
                    "model": {
                        "provider": "test-provider",
                        "model_id": "test-small-001",
                    },
                },
            ]
        )
        repo._store[("company", "agents")] = (agents, now)
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )

        # Replace _post_setup_reinit with a wrapper that swaps the
        # provider registry and records the call, simulating the real
        # reload without needing stored provider configs or a real
        # ProviderRegistry.from_config invocation.
        fresh_registry = ProviderRegistry({"reloaded-provider": stub})
        reinit_called = False

        async def _fake_reinit(state: object) -> None:
            nonlocal reinit_called
            reinit_called = True
            app_state.swap_provider_registry(fresh_registry)

        monkeypatch.setattr(
            "synthorg.api.controllers.setup._post_setup_reinit",
            _fake_reinit,
        )

        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 201
            assert resp.json()["data"]["setup_complete"] is True
            # _post_setup_reinit was invoked by complete_setup.
            assert reinit_called
            # The provider registry should have been swapped.
            assert app_state._provider_registry is fresh_registry
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)

    def test_complete_bootstraps_even_when_provider_reload_fails(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agent bootstrap runs even when provider reload raises."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "company_name")] = ("Test Corp", now)
        agents = json.dumps(
            [
                {
                    "name": "alice",
                    "role": "developer",
                    "department": "engineering",
                    "model": {
                        "provider": "test-provider",
                        "model_id": "test-small-001",
                    },
                },
            ]
        )
        repo._store[("company", "agents")] = (agents, now)
        stub = MagicMock(spec=BaseCompletionProvider)
        original_registry = app_state._provider_registry
        app_state._provider_registry = ProviderRegistry(
            {"test-provider": stub},
        )

        # Make provider config loading raise to simulate reload failure.
        monkeypatch.setattr(
            "synthorg.providers.registry.ProviderRegistry.from_config",
            MagicMock(side_effect=RuntimeError("provider config broken")),
        )

        try:
            resp = test_client.post("/api/v1/setup/complete")
            assert resp.status_code == 201
            assert resp.json()["data"]["setup_complete"] is True
            # Agent bootstrap should still have run despite provider
            # reload failure -- the two operations are independent.
            loop = asyncio.new_event_loop()
            try:
                agent_count = loop.run_until_complete(
                    app_state.agent_registry.agent_count(),
                )
            finally:
                loop.close()
            assert agent_count >= 1
        finally:
            app_state._provider_registry = original_registry
            repo._store.pop(("company", "company_name"), None)
            repo._store.pop(("company", "agents"), None)
            repo._store.pop(("api", "setup_complete"), None)


@pytest.mark.unit
class TestExtractTemplateDepartments:
    """Unit tests for the _load_template_safe + _departments_to_json helpers."""

    def test_valid_template(self) -> None:
        from synthorg.api.controllers.setup_agents import departments_to_json
        from synthorg.api.controllers.setup_helpers import (
            load_template_safe as _load_template_safe,
        )

        loaded = _load_template_safe("solo_founder")
        result = departments_to_json(loaded.template.departments)
        assert result != ""
        departments = json.loads(result)
        assert len(departments) >= 1
        assert departments[0]["name"] in {"executive", "engineering"}

    def test_invalid_template(self) -> None:
        from synthorg.api.controllers.setup_helpers import (
            load_template_safe as _load_template_safe,
        )
        from synthorg.core.domain_errors import NotFoundError

        with pytest.raises(NotFoundError):
            _load_template_safe("nonexistent_template")


def _setup_mock_providers(
    test_client: TestClient[Any],
) -> tuple[Any, Any]:
    """Wire up mock providers on the app state. Returns (app_state, original)."""
    mock_model = MagicMock()
    mock_model.id = "test-small-001"
    mock_model.alias = None
    mock_model.cost_per_1k_input = 0.01
    mock_model.cost_per_1k_output = 0.02
    mock_model.max_context = 200_000
    mock_model.estimated_latency_ms = 100
    mock_provider_config = MagicMock()
    mock_provider_config.models = (mock_model,)

    mock_mgmt = MagicMock()
    mock_mgmt.list_providers = AsyncMock(
        return_value={"test-provider": mock_provider_config},
    )

    app_state = test_client.app.state.app_state
    original = app_state._provider_management
    app_state._provider_management = mock_mgmt
    return app_state, original


@pytest.mark.unit
class TestSetupCompanyAutoAgents:
    """POST /api/v1/setup/company -- auto-create agents from template."""

    def test_template_creates_agents(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Company creation with template auto-creates agents."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            resp = test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "My Startup",
                    "template_name": "startup",
                },
            )
            assert resp.status_code == 201
            data = resp.json()["data"]
            assert data["agent_count"] >= 3
            assert len(data["agents"]) >= 3
            # Each agent should have a name, role, and model assignment.
            for agent in data["agents"]:
                assert agent["name"]
                assert agent["role"]
                assert agent["tier"] in {"large", "medium", "small"}
                assert agent["model_provider"], "model_provider must be set"
                assert agent["model_id"], "model_id must be set"
        finally:
            app_state._provider_management = original

    def test_blank_company_has_no_agents(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Blank company (no template) creates zero agents."""
        resp = test_client.post(
            "/api/v1/setup/company",
            json={"company_name": "Blank Corp"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["agent_count"] == 0
        assert data["agents"] == []


@pytest.mark.unit
class TestSetupAgentsList:
    """GET /api/v1/setup/agents -- list agents configured during setup."""

    def test_empty_when_no_agents(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    def test_tampered_cursor_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/agents?cursor=garbage")
        assert resp.status_code == 400

    def test_returns_agents_after_company_creation(
        self,
        test_client: TestClient[Any],
    ) -> None:
        app_state, original = _setup_mock_providers(test_client)
        try:
            # Create company with template.
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Test Startup",
                    "template_name": "solo_founder",
                },
            )
            # Now list agents.
            resp = test_client.get("/api/v1/setup/agents")
            assert resp.status_code == 200
            body = resp.json()
            agents = body["data"]
            assert len(agents) >= 1
            assert agents[0]["role"]
            assert body["pagination"]["limit"] >= len(agents)
        finally:
            app_state._provider_management = original

    def test_pagination_round_trip_with_limit_one(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """First page + cursor follow-up reaches the final page."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Test Startup",
                    "template_name": "solo_founder",
                },
            )
            first = test_client.get("/api/v1/setup/agents?limit=1").json()
            assert len(first["data"]) <= 1
            collected = list(first["data"])
            cursor = first["pagination"]["next_cursor"]
            # Pin the cross-page invariant so a future template shrink
            # (``solo_founder`` happens to seed two agents today; a
            # one-agent template would leave the loop body unexercised
            # and turn the test into a vacuous pass).
            assert cursor is not None, (
                "first page's next_cursor must be non-null so the "
                "cursor-follow-up loop is actually entered; the "
                "seeded template no longer crosses a page boundary"
            )
            while cursor:
                page = test_client.get(
                    f"/api/v1/setup/agents?limit=1&cursor={cursor}",
                ).json()
                collected.extend(page["data"])
                cursor = page["pagination"]["next_cursor"]
            # Strict equality (not length) so the test fails on
            # duplicates, gaps, or reordering across pages.  Build
            # ``full`` by walking pagination at a high limit so the
            # oracle is the entire dataset rather than the first
            # ``DEFAULT_LIMIT``-sized page; the previous single-GET
            # version compared two truncated views once the agent
            # count exceeded the default page size.
            full: list[dict[str, Any]] = []
            full_cursor: str | None = None
            while True:
                qs = (
                    "?limit=200"
                    if full_cursor is None
                    else f"?limit=200&cursor={full_cursor}"
                )
                page = test_client.get(f"/api/v1/setup/agents{qs}").json()
                full.extend(page["data"])
                full_cursor = page["pagination"]["next_cursor"]
                if full_cursor is None:
                    break
            assert collected == full
        finally:
            app_state._provider_management = original


@pytest.mark.unit
class TestSetupAgentModelUpdate:
    """PUT /api/v1/setup/agents/{index}/model -- reassign agent model."""

    def test_out_of_range_index(
        self,
        test_client: TestClient[Any],
    ) -> None:
        app_state, original = _setup_mock_providers(test_client)
        try:
            resp = test_client.put(
                "/api/v1/setup/agents/99/model",
                json={
                    "model_provider": "test-provider",
                    "model_id": "test-small-001",
                },
            )
            assert resp.status_code == 404
        finally:
            app_state._provider_management = original

    def test_successful_model_update(
        self,
        test_client: TestClient[Any],
    ) -> None:
        app_state, original = _setup_mock_providers(test_client)
        try:
            # Create company with template to get agents.
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Update Test",
                    "template_name": "solo_founder",
                },
            )
            # Update first agent's model.
            resp = test_client.put(
                "/api/v1/setup/agents/0/model",
                json={
                    "model_provider": "test-provider",
                    "model_id": "test-small-001",
                },
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["model_provider"] == "test-provider"
            assert data["model_id"] == "test-small-001"
            updated_name = data["name"]

            # Verify persistence: GET agents and check the update stuck.
            # Anchor on the updated agent's name so the assertion cannot
            # pass via a different agent that already carries the same
            # provider/model pair.
            get_resp = test_client.get("/api/v1/setup/agents")
            assert get_resp.status_code == 200
            agents = get_resp.json()["data"]
            assert any(
                a["name"] == updated_name
                and a["model_provider"] == "test-provider"
                and a["model_id"] == "test-small-001"
                for a in agents
            )
        finally:
            app_state._provider_management = original

    def test_invalid_provider_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        app_state, original = _setup_mock_providers(test_client)
        try:
            # Create agents first -- verify seed succeeded.
            seed = test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Validation Test",
                    "template_name": "solo_founder",
                },
            )
            assert seed.status_code == 201
            assert seed.json()["data"]["agent_count"] >= 1
            resp = test_client.put(
                "/api/v1/setup/agents/0/model",
                json={
                    "model_provider": "nonexistent-provider",
                    "model_id": "some-model",
                },
            )
            assert resp.status_code == 404
        finally:
            app_state._provider_management = original


@pytest.mark.unit
class TestUpdateAgentName:
    """PUT /api/v1/setup/agents/{index}/name -- rename an agent."""

    def test_successful_name_update(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Renaming an agent persists the new name."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Name Test",
                    "template_name": "solo_founder",
                },
            )
            resp = test_client.put(
                "/api/v1/setup/agents/0/name",
                json={"name": "New Agent Name"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["name"] == "New Agent Name"

            # Verify persistence -- agents are name-sorted so search by value.
            get_resp = test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(a["name"] == "New Agent Name" for a in agents)
        finally:
            app_state._provider_management = original

    def test_out_of_range_index(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Out-of-range index returns 404."""
        resp = test_client.put(
            "/api/v1/setup/agents/99/name",
            json={"name": "Some Name"},
        )
        assert resp.status_code == 404

    def test_blank_name_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Empty or whitespace-only name is rejected by validation."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Blank Name Test",
                    "template_name": "solo_founder",
                },
            )
            resp = test_client.put(
                "/api/v1/setup/agents/0/name",
                json={"name": "   "},
            )
            assert resp.status_code == 400
        finally:
            app_state._provider_management = original


@pytest.mark.unit
class TestRandomizeAgentName:
    """POST /api/v1/setup/agents/{index}/randomize-name."""

    def test_randomize_generates_new_name(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Randomize endpoint generates a non-empty name."""
        # Mock generate_auto_name to avoid heavy multi-locale Faker
        # initialization that causes xdist worker crashes on Windows.
        monkeypatch.setattr(
            "synthorg.templates.presets.generate_auto_name",
            lambda role, *, seed=None, locales=None: "Ada Lovelace",
        )
        app_state, original = _setup_mock_providers(test_client)
        try:
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Randomize Test",
                    "template_name": "solo_founder",
                },
            )
            resp = test_client.post(
                "/api/v1/setup/agents/0/randomize-name",
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["name"] == "Ada Lovelace"

            # Verify persistence -- agents are name-sorted so search by value.
            get_resp = test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(a["name"] == "Ada Lovelace" for a in agents)
        finally:
            app_state._provider_management = original

    def test_out_of_range_index(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Out-of-range index returns 404."""
        resp = test_client.post(
            "/api/v1/setup/agents/99/randomize-name",
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestGetAvailableLocales:
    """GET /api/v1/setup/name-locales/available -- list available locales."""

    def test_returns_regions_and_display_names(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/name-locales/available")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "regions" in data
        assert "display_names" in data
        assert isinstance(data["regions"], dict)
        assert isinstance(data["display_names"], dict)
        # Verify at least some regions and display names are present.
        assert len(data["regions"]) >= 5
        assert len(data["display_names"]) >= 50


@pytest.mark.unit
class TestGetNameLocales:
    """GET /api/v1/setup/name-locales -- get current locale configuration."""

    def test_returns_all_sentinel_when_not_configured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns the ``__all__`` sentinel when no DB preference is stored.

        The endpoint returns the raw sentinel so the frontend can show
        the "All (worldwide)" toggle as ON.  Resolution to concrete
        locale codes happens only in the name-generation path.
        """
        resp = test_client.get("/api/v1/setup/name-locales")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["locales"] == ["__all__"]

    def test_returns_stored_locales_when_configured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns stored locales when the setting is in the DB."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "fr_FR"]),
            now,
        )
        try:
            resp = test_client.get("/api/v1/setup/name-locales")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["locales"] == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    def test_returns_all_sentinel_when_explicitly_stored(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns ``__all__`` sentinel when it is explicitly stored in DB.

        Verifies the round-trip: saving ``["__all__"]`` then reading it
        back returns the sentinel, not the full expanded list of
        concrete locale codes.
        """
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["__all__"]),
            now,
        )
        try:
            resp = test_client.get("/api/v1/setup/name-locales")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["locales"] == ["__all__"]
        finally:
            repo._store.pop(("company", "name_locales"), None)


@pytest.mark.unit
class TestSaveNameLocales:
    """PUT /api/v1/setup/name-locales -- save locale preferences."""

    def test_saves_valid_locales(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["en_US", "de_DE"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["locales"] == ["en_US", "de_DE"]

    def test_saves_all_sentinel(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["__all__"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["locales"] == ["__all__"]

    def test_rejects_mixed_sentinel_with_explicit_codes(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Mixing __all__ with explicit locale codes returns 422."""
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["__all__", "en_US"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False

    def test_rejects_invalid_locale_codes(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["en_US", "invalid_XX"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False

    def test_rejects_empty_list(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Empty list is rejected by Pydantic min_length=1."""
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": []},
        )
        assert resp.status_code == 400

    def test_rejects_save_after_setup_complete(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Saving locales after setup is complete returns 409."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("api", "setup_complete")] = ("true", now)
        try:
            resp = test_client.put(
                "/api/v1/setup/name-locales",
                json={"locales": ["en_US"]},
            )
            assert resp.status_code == 409
        finally:
            repo._store.pop(("api", "setup_complete"), None)


@pytest.mark.unit
class TestCheckHasNameLocales:
    """Unit tests for the _check_has_name_locales helper."""

    async def test_returns_false_when_not_in_db(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Code default resolves as non-DATABASE source, returns False."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        # Ensure the key is absent from DB so code default kicks in.
        repo = app_state.persistence._settings_repo
        repo._store.pop(("company", "name_locales"), None)

        result = await _check_has_name_locales(settings_svc)
        assert result is False

    async def test_returns_true_when_db_sourced_and_nonempty(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US"]),
            now,
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is True
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_false_on_generic_exception(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns False when get_entry raises a generic exception."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            spec=original,
            side_effect=RuntimeError("db connection lost"),
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is False
        finally:
            settings_svc.get_entry = original

    async def test_returns_false_on_setting_not_found_error(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns False when get_entry raises SettingNotFoundError."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )
        from synthorg.settings.errors import SettingNotFoundError

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            spec=original,
            side_effect=SettingNotFoundError("company/name_locales"),
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is False
        finally:
            settings_svc.get_entry = original


@pytest.mark.unit
class TestReadNameLocales:
    """Unit tests for the _read_name_locales helper."""

    async def test_returns_all_locales_when_db_absent_and_code_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """When DB key is absent, code default ["__all__"] resolves to all."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )
        from synthorg.templates.locales import ALL_LATIN_LOCALES

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        repo._store.pop(("company", "name_locales"), None)

        result = await _read_name_locales(settings_svc)
        # Code default is ["__all__"], resolve_locales returns all.
        assert result == list(ALL_LATIN_LOCALES)

    async def test_returns_none_when_setting_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns None when get_entry raises SettingNotFoundError."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )
        from synthorg.settings.errors import SettingNotFoundError

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            spec=original,
            side_effect=SettingNotFoundError("company/name_locales"),
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            settings_svc.get_entry = original

    async def test_returns_resolved_locales_when_valid(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "fr_FR"]),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_none_on_json_decode_error(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            "not-valid-json{{{",
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_none_on_non_list_json(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps({"not": "a list"}),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_filters_invalid_locales(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "invalid_XX", "fr_FR"]),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_resolve_false_returns_sentinel_as_is(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """With resolve=False, the __all__ sentinel passes through raw."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["__all__"]),
            now,
        )
        try:
            result = await _read_name_locales(
                settings_svc,
                resolve=False,
            )
            assert result == ["__all__"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_resolve_false_skips_validation_filtering(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """With resolve=False, invalid codes are not filtered out."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "invalid_XX"]),
            now,
        )
        try:
            result = await _read_name_locales(
                settings_svc,
                resolve=False,
            )
            assert result == ["en_US", "invalid_XX"]
        finally:
            repo._store.pop(("company", "name_locales"), None)


@pytest.mark.unit
class TestUpdateAgentPersonality:
    """PUT /api/v1/setup/agents/{index}/personality -- update personality."""

    def test_update_personality_happy_path(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Updating an agent's personality persists the new preset."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            # Create company with template to get agents.
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Personality Test",
                    "template_name": "solo_founder",
                },
            )
            resp = test_client.put(
                "/api/v1/setup/agents/0/personality",
                json={"personality_preset": "visionary_leader"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["personality_preset"] == "visionary_leader"
            updated_name = data["name"]

            # Verify persistence: anchor on the updated agent's name so
            # the assertion cannot pass via a different agent that
            # already carries the visionary_leader preset.
            get_resp = test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(
                agent["name"] == updated_name
                and agent["personality_preset"] == "visionary_leader"
                for agent in agents
            )
        finally:
            app_state._provider_management = original

    def test_update_personality_invalid_preset(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Invalid personality preset name is rejected with 400."""
        app_state, original = _setup_mock_providers(test_client)
        try:
            test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Invalid Preset Test",
                    "template_name": "solo_founder",
                },
            )
            resp = test_client.put(
                "/api/v1/setup/agents/0/personality",
                json={"personality_preset": "nonexistent_preset"},
            )
            assert resp.status_code == 400
        finally:
            app_state._provider_management = original

    def test_update_personality_out_of_range(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Out-of-range agent index returns 404."""
        resp = test_client.put(
            "/api/v1/setup/agents/999/personality",
            json={"personality_preset": "visionary_leader"},
        )
        assert resp.status_code == 404

    def test_update_personality_after_complete(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Updating personality after setup is complete returns 409."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("api", "setup_complete")] = ("true", now)
        try:
            resp = test_client.put(
                "/api/v1/setup/agents/0/personality",
                json={"personality_preset": "visionary_leader"},
            )
            assert resp.status_code == 409
        finally:
            repo._store.pop(("api", "setup_complete"), None)


@pytest.mark.unit
class TestListPersonalityPresets:
    """GET /api/v1/setup/personality-presets -- list personality presets."""

    def test_list_presets_returns_non_empty(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Personality presets endpoint returns a non-empty paginated list."""
        resp = test_client.get("/api/v1/setup/personality-presets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        presets = body["data"]
        assert len(presets) >= 1
        assert "next_cursor" in body["pagination"]

    def test_list_presets_field_shape(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Each preset has ``name`` and ``description`` fields."""
        resp = test_client.get("/api/v1/setup/personality-presets")
        body = resp.json()
        for preset in body["data"]:
            assert "name" in preset
            assert "description" in preset
            assert isinstance(preset["name"], str)
            assert isinstance(preset["description"], str)
            assert preset["name"].strip() != ""

    def test_list_presets_round_trip_with_cursor(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Walking pages with limit=1 returns every preset exactly once."""
        # Build the full list by walking pagination at a high limit so
        # ``full`` is the entire dataset rather than the first
        # ``DEFAULT_LIMIT``-sized page.  The previous implementation
        # broke silently the moment the preset registry grew past the
        # default page size, because the round-trip check was then
        # comparing two truncated views.
        full: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            qs = "?limit=200" if cursor is None else f"?limit=200&cursor={cursor}"
            page = test_client.get(f"/api/v1/setup/personality-presets{qs}").json()
            full.extend(page["data"])
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        if len(full) < 2:
            pytest.skip("need at least two presets for cursor round-trip")
        first = test_client.get(
            "/api/v1/setup/personality-presets?limit=1",
        ).json()
        assert len(first["data"]) == 1
        collected = list(first["data"])
        cursor = first["pagination"]["next_cursor"]
        assert cursor is not None
        while cursor:
            page = test_client.get(
                f"/api/v1/setup/personality-presets?limit=1&cursor={cursor}",
            ).json()
            collected.extend(page["data"])
            cursor = page["pagination"]["next_cursor"]
        # Strict equality already encodes "no duplicates, no gaps,
        # stable order"; loosening this check would require explicit
        # invariant assertions but as written this single line covers
        # the round-trip property in full.
        assert collected == full

    def test_list_presets_tampered_cursor_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/setup/personality-presets?cursor=garbage",
        )
        assert resp.status_code == 400


# ``TestReadHasGpuSetting`` moved to test_setup_has_gpu.py to keep this
# file under the project's file-length guideline.
