"""Tests for setup wizard agent CRUD operations.

Covers agent creation, listing, model updates, name updates,
randomize-name, auto-agents from templates, and the
agent_dict_to_summary helper.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.providers.state import ProvidersStateSlice
from tests._shared import JsonDict, LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.controllers.conftest import mock_providers, setup_mock_providers


@pytest.mark.unit
class TestSetupAgent:
    """POST /api/v1/setup/agent -- create agent."""

    async def test_nonexistent_provider(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/setup/agent",
            json={
                "name": "Alice Chen",
                "role": "CEO",
                "model_provider": "nonexistent",
                "model_id": "model-001",
            },
        )
        assert resp.status_code == 404

    async def test_invalid_personality_preset(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/setup/agent",
            json={
                "name": "Alice Chen",
                "role": "CEO",
                "personality_preset": "nonexistent_preset",
                "model_provider": "test",
                "model_id": "model-001",
            },
        )
        # Pydantic model_validator returns 400
        assert resp.status_code == 400

    async def test_requires_write_access(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        saved_headers = dict(async_test_client.headers)
        async_test_client.headers.update(make_auth_headers("observer"))
        try:
            resp = await async_test_client.post(
                "/api/v1/setup/agent",
                json={
                    "name": "Alice Chen",
                    "role": "CEO",
                    "model_provider": "test",
                    "model_id": "model-001",
                },
            )
            assert resp.status_code == 403
        finally:
            async_test_client.headers.update(saved_headers)

    async def test_successful_agent_creation(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Happy path: provider and model exist, agent is created."""
        # Build a mock provider config with a test model.
        mock_model = MagicMock()
        mock_model.id = "test-small-001"
        mock_model.alias = None
        mock_provider_config = MagicMock()
        mock_provider_config.models = [mock_model]

        mock_mgmt = MagicMock()
        mock_mgmt.list_providers = AsyncMock(
            return_value={"test-provider": mock_provider_config},
        )

        app_state = async_test_client.app.state.app_state
        original_mgmt = app_state.slice(ProvidersStateSlice).management
        app_state.wire(ProvidersStateSlice, management=mock_mgmt)
        try:
            resp = await async_test_client.post(
                "/api/v1/setup/agent",
                json={
                    "name": "agent-ceo-001",
                    "role": "CEO",
                    "model_provider": "test-provider",
                    "model_id": "test-small-001",
                },
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["success"] is True
            data = body["data"]
            assert data["name"] == "agent-ceo-001"
            assert data["role"] == "CEO"
            assert data["department"] == "engineering"
            assert data["model_provider"] == "test-provider"
            assert data["model_id"] == "test-small-001"
        finally:
            app_state.wire(ProvidersStateSlice, management=original_mgmt)


@pytest.mark.unit
class TestAgentDictToSummary:
    """Unit tests for agent_dict_to_summary empty-to-None conversion."""

    def test_empty_strings_become_none(self) -> None:
        from synthorg.api.controllers.setup_agents import (
            agent_dict_to_summary,
        )

        agent: JsonDict = {
            "name": "Alice",
            "role": "Developer",
            "department": "Engineering",
            "level": "",
            "tier": "medium",
            "personality_preset": None,
            "model": {"provider": "", "model_id": ""},
        }
        summary = agent_dict_to_summary(agent)
        assert summary.level is None
        assert summary.model_provider is None
        assert summary.model_id is None

    def test_whitespace_strings_become_none(self) -> None:
        from synthorg.api.controllers.setup_agents import (
            agent_dict_to_summary,
        )

        agent: JsonDict = {
            "name": "Bob",
            "role": "QA",
            "department": "Engineering",
            "level": "   ",
            "tier": "small",
            "personality_preset": None,
            "model": {"provider": "  ", "model_id": "  "},
        }
        summary = agent_dict_to_summary(agent)
        assert summary.level is None
        assert summary.model_provider is None
        assert summary.model_id is None

    def test_valid_strings_preserved(self) -> None:
        from synthorg.api.controllers.setup_agents import (
            agent_dict_to_summary,
        )

        agent: JsonDict = {
            "name": "Carol",
            "role": "PM",
            "department": "Product",
            "level": "senior",
            "tier": "large",
            "personality_preset": "visionary_leader",
            "model": {"provider": "test-provider", "model_id": "test-model-001"},
        }
        summary = agent_dict_to_summary(agent)
        assert summary.level == "senior"
        assert summary.model_provider == "test-provider"
        assert summary.model_id == "test-model-001"


@pytest.mark.unit
class TestSetupCompanyAutoAgents:
    """POST /api/v1/setup/company -- auto-create agents from template."""

    async def test_template_creates_agents(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Company creation with template auto-creates agents."""
        app_state, original = setup_mock_providers(async_test_client)
        try:
            resp = await async_test_client.post(
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
            app_state.wire(ProvidersStateSlice, management=original)

    async def test_blank_company_has_no_agents(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Blank company (no template) creates zero agents."""
        resp = await async_test_client.post(
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

    async def test_empty_when_no_agents(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/setup/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    async def test_returns_agents_after_company_creation(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state, original = setup_mock_providers(async_test_client)
        try:
            # Create company with template.
            await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Test Startup",
                    "template_name": "solo_founder",
                },
            )
            # Now list agents.
            resp = await async_test_client.get("/api/v1/setup/agents")
            assert resp.status_code == 200
            agents = resp.json()["data"]
            assert len(agents) >= 1
            assert agents[0]["role"]
        finally:
            app_state.wire(ProvidersStateSlice, management=original)


@pytest.mark.unit
class TestSetupAgentModelUpdate:
    """PUT /api/v1/setup/agents/{index}/model -- reassign agent model."""

    async def test_out_of_range_index(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state, original = setup_mock_providers(async_test_client)
        try:
            resp = await async_test_client.put(
                "/api/v1/setup/agents/99/model",
                json={
                    "model_provider": "test-provider",
                    "model_id": "test-small-001",
                },
            )
            assert resp.status_code == 404
        finally:
            app_state.wire(ProvidersStateSlice, management=original)

    async def test_successful_model_update(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state, original = setup_mock_providers(async_test_client)
        try:
            # Create company with template to get agents.
            await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Update Test",
                    "template_name": "solo_founder",
                },
            )
            # Update first agent's model.
            resp = await async_test_client.put(
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
            # accidentally pass via a different agent that already
            # carries the same provider/model pair.
            get_resp = await async_test_client.get("/api/v1/setup/agents")
            assert get_resp.status_code == 200
            agents = get_resp.json()["data"]
            assert any(
                a["name"] == updated_name
                and a["model_provider"] == "test-provider"
                and a["model_id"] == "test-small-001"
                for a in agents
            )
        finally:
            app_state.wire(ProvidersStateSlice, management=original)

    async def test_invalid_provider_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state, original = setup_mock_providers(async_test_client)
        try:
            # Create agents first -- verify seed succeeded.
            seed = await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Validation Test",
                    "template_name": "solo_founder",
                },
            )
            assert seed.status_code == 201
            assert seed.json()["data"]["agent_count"] >= 1
            resp = await async_test_client.put(
                "/api/v1/setup/agents/0/model",
                json={
                    "model_provider": "nonexistent-provider",
                    "model_id": "some-model",
                },
            )
            assert resp.status_code == 404
        finally:
            app_state.wire(ProvidersStateSlice, management=original)


@pytest.mark.unit
class TestUpdateAgentName:
    """PUT /api/v1/setup/agents/{index}/name -- rename an agent."""

    async def test_successful_name_update(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Renaming an agent persists the new name."""
        app_state, original = setup_mock_providers(async_test_client)
        try:
            await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Name Test",
                    "template_name": "solo_founder",
                },
            )
            resp = await async_test_client.put(
                "/api/v1/setup/agents/0/name",
                json={"name": "New Agent Name"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["name"] == "New Agent Name"

            # Verify persistence -- search by value, not by index.
            get_resp = await async_test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(a["name"] == "New Agent Name" for a in agents)
        finally:
            app_state.wire(ProvidersStateSlice, management=original)

    async def test_out_of_range_index(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Out-of-range index returns 404."""
        resp = await async_test_client.put(
            "/api/v1/setup/agents/99/name",
            json={"name": "Some Name"},
        )
        assert resp.status_code == 404

    async def test_blank_name_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Empty or whitespace-only name is rejected by validation."""
        app_state, original = setup_mock_providers(async_test_client)
        try:
            await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Blank Name Test",
                    "template_name": "solo_founder",
                },
            )
            resp = await async_test_client.put(
                "/api/v1/setup/agents/0/name",
                json={"name": "   "},
            )
            assert resp.status_code == 400
        finally:
            app_state.wire(ProvidersStateSlice, management=original)


@pytest.mark.unit
class TestRandomizeAgentName:
    """POST /api/v1/setup/agents/{index}/randomize-name."""

    async def test_randomize_generates_new_name(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Randomize endpoint generates a non-empty name."""
        with mock_providers(async_test_client):
            # Seed a single agent directly: randomize only needs one
            # existing agent at index 0, so the cheaper per-agent create
            # path avoids the heavier template-based company seeding.
            create = await async_test_client.post(
                "/api/v1/setup/agent",
                json={
                    "name": "agent-seed-001",
                    "role": "CEO",
                    "model_provider": "test-provider",
                    "model_id": "test-small-001",
                },
            )
            assert create.status_code == 201

            # Pin a single name locale so the randomize endpoint builds a
            # one-locale Faker instead of the all-Latin-script default,
            # whose cold provider init is the slowest part of this test.
            locales = await async_test_client.put(
                "/api/v1/setup/name-locales",
                json={"locales": ["en_US"]},
            )
            assert locales.status_code == 200

            resp = await async_test_client.post(
                "/api/v1/setup/agents/0/randomize-name",
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            # The randomiser must actually replace the seeded name, not echo
            # it back: a no-op endpoint would still pass the non-empty/length
            # checks below.
            assert data["name"] != "agent-seed-001"
            assert data["name"] != ""
            assert len(data["name"]) >= 3

            # Verify persistence -- search by value, not by index.
            get_resp = await async_test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(a["name"] == data["name"] for a in agents)

    async def test_out_of_range_index(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Out-of-range index returns 404."""
        resp = await async_test_client.post(
            "/api/v1/setup/agents/99/randomize-name",
        )
        assert resp.status_code == 404
