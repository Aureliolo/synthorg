"""Tests for PersonalityPresetController."""

import pytest

from tests._shared import JsonDict, LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


def _make_valid_preset_body(
    name: str = "my_custom_preset",
    **overrides: object,
) -> JsonDict:
    """Build a valid create-preset request body."""
    body: JsonDict = {
        "name": name,
        "traits": ["friendly", "curious"],
        "communication_style": "warm",
        "risk_tolerance": "medium",
        "creativity": "high",
        "description": "A custom test preset",
        "openness": 0.8,
        "conscientiousness": 0.6,
        "extraversion": 0.7,
        "agreeableness": 0.9,
        "stress_response": 0.5,
        "decision_making": "consultative",
        "collaboration": "team",
        "verbosity": "balanced",
        "conflict_approach": "collaborate",
    }
    body.update(overrides)
    return body


# ── Discovery endpoints ───────────────────────────────────────


@pytest.mark.unit
class TestListPresets:
    async def test_lists_all_builtins(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/personalities/presets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    async def test_pagination_works(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/personalities/presets?offset=0&limit=5"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 5
        assert body["pagination"]["limit"] == 5

    async def test_each_item_has_required_fields(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/personalities/presets?limit=3")
        body = resp.json()
        for item in body["data"]:
            assert "name" in item
            assert "description" in item
            assert "traits" in item
            assert "source" in item
            assert item["source"] in ("builtin", "custom")

    async def test_observer_can_read(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/personalities/presets",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 200


@pytest.mark.unit
class TestGetPreset:
    async def test_get_builtin_preset(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/personalities/presets/visionary_leader"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "visionary_leader"
        assert body["data"]["source"] == "builtin"
        assert "openness" in body["data"]
        assert "traits" in body["data"]

    async def test_get_nonexistent_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/personalities/presets/nonexistent_preset_xyz"
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    async def test_observer_can_read_detail(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/personalities/presets/pragmatic_builder",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 200


@pytest.mark.unit
class TestGetSchema:
    async def test_returns_json_schema(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/personalities/schema")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        schema = body["data"]
        assert "properties" in schema
        assert "openness" in schema["properties"]


# ── CRUD endpoints ────────────────────────────────────────────


@pytest.mark.unit
class TestCreatePreset:
    async def test_create_custom_preset(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body()
        resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["name"] == "my_custom_preset"
        assert data["data"]["source"] == "custom"

    async def test_create_with_builtin_name_returns_409(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="visionary_leader")
        resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_create_with_invalid_openness_returns_400(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(openness=2.0)
        resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_create_duplicate_returns_409(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="dup_test")
        first_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert first_resp.status_code == 201
        resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_observer_cannot_create(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body()
        resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403

    async def test_created_preset_appears_in_list(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="listed_preset")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        resp = await async_test_client.get("/api/v1/personalities/presets?limit=200")
        names = [p["name"] for p in resp.json()["data"]]
        assert "listed_preset" in names

    async def test_created_preset_gettable(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="gettable_preset")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        resp = await async_test_client.get(
            "/api/v1/personalities/presets/gettable_preset"
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "custom"


@pytest.mark.unit
class TestUpdatePreset:
    async def test_update_custom_preset(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Create first
        body = _make_valid_preset_body(name="updatable")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        # Update
        update_body = {k: v for k, v in body.items() if k != "name"}
        update_body["openness"] = 0.1
        resp = await async_test_client.put(
            "/api/v1/personalities/presets/updatable",
            json=update_body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["openness"] == 0.1

    async def test_update_builtin_returns_409(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body()
        update_body = {k: v for k, v in body.items() if k != "name"}
        resp = await async_test_client.put(
            "/api/v1/personalities/presets/visionary_leader",
            json=update_body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_update_nonexistent_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body()
        update_body = {k: v for k, v in body.items() if k != "name"}
        resp = await async_test_client.put(
            "/api/v1/personalities/presets/nonexistent_xyz",
            json=update_body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_observer_cannot_update(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="obs_update_test")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        update_body = {k: v for k, v in body.items() if k != "name"}
        resp = await async_test_client.put(
            "/api/v1/personalities/presets/obs_update_test",
            json=update_body,
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403

    async def test_update_with_invalid_config_returns_400(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = _make_valid_preset_body(name="invalid_update")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        update_body = {k: v for k, v in body.items() if k != "name"}
        update_body["openness"] = 2.0
        resp = await async_test_client.put(
            "/api/v1/personalities/presets/invalid_update",
            json=update_body,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestDeletePreset:
    async def test_delete_custom_preset(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Create first
        body = _make_valid_preset_body(name="deletable")
        create_resp = await async_test_client.post(
            "/api/v1/personalities/presets",
            json=body,
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        resp = await async_test_client.delete(
            "/api/v1/personalities/presets/deletable",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        # Verify it's gone
        get_resp = await async_test_client.get(
            "/api/v1/personalities/presets/deletable"
        )
        assert get_resp.status_code == 404

    async def test_delete_builtin_returns_409(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/personalities/presets/visionary_leader",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_delete_nonexistent_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/personalities/presets/nonexistent_xyz",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_observer_cannot_delete(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/personalities/presets/visionary_leader",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403
