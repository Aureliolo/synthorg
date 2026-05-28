"""Unit tests for settings API controller."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """CEO-role auth headers."""
    return make_auth_headers("ceo")


@pytest.fixture
def observer_headers() -> dict[str, str]:
    """Observer-role auth headers."""
    return make_auth_headers("observer")


@pytest.mark.unit
class TestSettingsController:
    """Tests for settings REST endpoints."""

    async def test_list_all_settings(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get("/api/v1/settings", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) > 0
        assert "pagination" in body
        assert body["pagination"]["limit"] == 50

    async def test_list_all_settings_explicit_limit(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings?limit=3",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 3
        assert body["pagination"]["limit"] == 3
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"] is not None

    async def test_list_all_settings_cursor_chain_returns_disjoint_pages(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        first = await async_test_client.get(
            "/api/v1/settings",
            params={"limit": 3},
            headers=auth_headers,
        )
        assert first.status_code == 200, first.text
        body = first.json()
        first_ids = [
            (e["definition"]["namespace"], e["definition"]["key"]) for e in body["data"]
        ]
        cursor = body["pagination"]["next_cursor"]
        assert cursor is not None

        second = await async_test_client.get(
            "/api/v1/settings",
            params={"limit": 3, "cursor": cursor},
            headers=auth_headers,
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        second_ids = [
            (e["definition"]["namespace"], e["definition"]["key"])
            for e in second_body["data"]
        ]
        assert set(first_ids).isdisjoint(second_ids)

    async def test_list_all_settings_invalid_cursor_is_400(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings",
            params={"cursor": "not-a-real-cursor"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_list_all_settings_stable_ordering(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        first_resp = await async_test_client.get(
            "/api/v1/settings",
            params={"limit": 5},
            headers=auth_headers,
        )
        assert first_resp.status_code == 200, first_resp.text
        first = first_resp.json()["data"]
        second_resp = await async_test_client.get(
            "/api/v1/settings",
            params={"limit": 5},
            headers=auth_headers,
        )
        assert second_resp.status_code == 200, second_resp.text
        second = second_resp.json()["data"]
        assert first == second

    async def test_get_namespace_settings(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/budget", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        for entry in body["data"]:
            assert entry["definition"]["namespace"] == "budget"

    async def test_get_single_setting(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/budget/total_monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["definition"]["key"] == "total_monthly"

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/v1/settings/budget/nonexistent",
            "/api/v1/settings/nonexistent_ns",
            "/api/v1/settings/_schema/nonexistent_ns",
        ],
    )
    async def test_unknown_resource_returns_404(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
        endpoint: str,
    ) -> None:
        resp = await async_test_client.get(endpoint, headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_setting(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.put(
            "/api/v1/settings/budget/total_monthly",
            json={"value": "200.0"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["value"] == "200.0"
        assert body["data"]["source"] == "db"

    async def test_update_validates_value(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.put(
            "/api/v1/settings/budget/total_monthly",
            json={"value": "not-a-number"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_update_unknown_setting_returns_404(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.put(
            "/api/v1/settings/budget/nonexistent",
            json={"value": "100"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_delete_setting(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        await async_test_client.put(
            "/api/v1/settings/budget/total_monthly",
            json={"value": "200.0"},
            headers=auth_headers,
        )
        resp = await async_test_client.delete(
            "/api/v1/settings/budget/total_monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 204
        assert resp.content == b""

    async def test_delete_unknown_setting_returns_404(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/settings/budget/nonexistent",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_get_full_schema(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/_schema",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) > 0

    async def test_get_namespace_schema(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/_schema/budget",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        for defn in body["data"]:
            assert defn["namespace"] == "budget"

    async def test_observer_can_read(
        self, async_test_client: LoopAsyncClient, observer_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings",
            headers=observer_headers,
        )
        assert resp.status_code == 200

    async def test_observer_cannot_write(
        self, async_test_client: LoopAsyncClient, observer_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.put(
            "/api/v1/settings/budget/total_monthly",
            json={"value": "200.0"},
            headers=observer_headers,
        )
        assert resp.status_code == 403

    async def test_observer_cannot_delete(
        self, async_test_client: LoopAsyncClient, observer_headers: dict[str, str]
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/settings/budget/total_monthly",
            headers=observer_headers,
        )
        assert resp.status_code == 403

    async def test_oversized_namespace_rejected(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        long_ns = "x" * 65
        resp = await async_test_client.get(
            f"/api/v1/settings/{long_ns}",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_oversized_key_rejected(
        self, async_test_client: LoopAsyncClient, auth_headers: dict[str, str]
    ) -> None:
        long_key = "x" * 129
        resp = await async_test_client.get(
            f"/api/v1/settings/budget/{long_key}",
            headers=auth_headers,
        )
        assert resp.status_code == 400


# -- Security config export/import tests ─────────────────────────


@pytest.mark.unit
class TestSecurityConfigExportImport:
    async def test_export_returns_config(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/security/export",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "config" in data
        assert "exported_at" in data
        assert "enabled" in data["config"]

    async def test_export_warning_when_no_custom_policies(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/security/export",
            headers=auth_headers,
        )
        body = resp.json()
        assert body["data"]["custom_policies_warning"] is None

    async def test_import_valid_config(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        # First export to get valid config shape
        export_resp = await async_test_client.get(
            "/api/v1/settings/security/export",
            headers=auth_headers,
        )
        config = export_resp.json()["data"]["config"]

        # Import the same config
        resp = await async_test_client.post(
            "/api/v1/settings/security/import",
            json={"config": config},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["config"]["enabled"] == config["enabled"]

    async def test_import_rejects_invalid_config(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/security/import",
            json={"config": {"enabled": "not-a-bool"}},
            headers=auth_headers,
        )
        # Domain ``ValidationError`` maps to 422 via the central
        # exception handler, not the legacy 400 the manual
        # ``ClientException`` site emitted.
        assert resp.status_code == 422

    async def test_import_requires_ceo_or_manager(
        self,
        async_test_client: LoopAsyncClient,
        observer_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/security/import",
            json={"config": {}},
            headers=observer_headers,
        )
        assert resp.status_code == 403

    async def test_round_trip(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Export, then import, then export again -- configs match."""
        resp1 = await async_test_client.get(
            "/api/v1/settings/security/export",
            headers=auth_headers,
        )
        config1 = resp1.json()["data"]["config"]

        await async_test_client.post(
            "/api/v1/settings/security/import",
            json={"config": config1},
            headers=auth_headers,
        )

        resp2 = await async_test_client.get(
            "/api/v1/settings/security/export",
            headers=auth_headers,
        )
        config2 = resp2.json()["data"]["config"]
        assert config1 == config2
