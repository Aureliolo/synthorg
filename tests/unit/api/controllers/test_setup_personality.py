"""Tests for setup wizard personality preset operations.

Covers agent personality updates and personality preset listing.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.controllers.conftest import setup_mock_providers


@pytest.mark.unit
class TestUpdateAgentPersonality:
    """PUT /api/v1/setup/agents/{index}/personality -- update personality."""

    def test_update_personality_happy_path(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Updating an agent's personality persists the new preset."""
        app_state, original = setup_mock_providers(test_client)
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

            # Verify persistence (agents are returned in name-sorted order
            # so use ``any`` rather than indexing by insertion position).
            get_resp = test_client.get("/api/v1/setup/agents")
            agents = get_resp.json()["data"]
            assert any(
                agent["personality_preset"] == "visionary_leader" for agent in agents
            )
        finally:
            app_state._provider_management = original

    def test_update_personality_invalid_preset(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Invalid personality preset name is rejected with 400."""
        app_state, original = setup_mock_providers(test_client)
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
        assert len(body["data"]) >= 1

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
