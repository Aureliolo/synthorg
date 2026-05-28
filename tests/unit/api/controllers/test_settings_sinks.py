"""Unit tests for settings sink API endpoints."""

import json

import pytest

from synthorg.api.controllers.settings import _sink_identifier
from synthorg.observability.config import DEFAULT_SINKS
from synthorg.observability.sink_config_builder import CONSOLE_SINK_ID
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


@pytest.fixture
def manager_headers() -> dict[str, str]:
    """Manager-role auth headers."""
    return make_auth_headers("manager")


@pytest.mark.unit
class TestListSinks:
    """Tests for GET /settings/observability/sinks."""

    async def test_returns_default_sinks(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        sinks = body["data"]
        assert isinstance(sinks, list)
        # Should have at least the default sinks (console + 10 files)
        assert len(sinks) >= len(DEFAULT_SINKS)

    async def test_all_defaults_marked_as_default(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        body = resp.json()
        sinks = body["data"]

        # Collect identifiers of default sinks via the canonical
        # helper so the hash format stays in lockstep with the
        # endpoint output.
        default_ids: set[str] = {_sink_identifier(s) for s in DEFAULT_SINKS}

        for sink in sinks:
            if sink["identifier"] in default_ids:
                assert sink["is_default"] is True

    async def test_console_sink_present(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        body = resp.json()
        sinks = body["data"]

        console_sinks = [s for s in sinks if s["identifier"] == CONSOLE_SINK_ID]
        assert len(console_sinks) == 1
        assert console_sinks[0]["sink_type"] == "console"
        assert console_sinks[0]["is_default"] is True
        assert console_sinks[0]["enabled"] is True

    async def test_sink_dict_fields(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        body = resp.json()
        sinks = body["data"]

        expected_keys = {
            "identifier",
            "sink_type",
            "level",
            "json_format",
            "rotation",
            "is_default",
            "enabled",
            "routing_prefixes",
        }
        for sink in sinks:
            assert set(sink.keys()) == expected_keys

    async def test_file_sink_has_rotation(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        body = resp.json()
        sinks = body["data"]

        file_sinks = [s for s in sinks if s["sink_type"] == "file"]
        assert len(file_sinks) > 0
        for fs in file_sinks:
            if fs["enabled"]:
                assert fs["rotation"] is not None
                assert "strategy" in fs["rotation"]
                assert "max_bytes" in fs["rotation"]
                assert "backup_count" in fs["rotation"]

    async def test_observer_can_read_sinks(
        self,
        async_test_client: LoopAsyncClient,
        observer_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=observer_headers,
        )
        assert resp.status_code == 200

    async def test_pagination_round_trip(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Walking pages with limit=1 enumerates every sink exactly once."""
        full = (
            await async_test_client.get(
                "/api/v1/settings/observability/sinks",
                headers=auth_headers,
            )
        ).json()["data"]
        # Fail loudly if the default sink list ever shrinks below the
        # two items this round-trip needs; a runtime ``pytest.skip``
        # would turn a fixture / endpoint regression into a green build.
        assert len(full) >= 2, (
            "default sink list must expose at least two sinks for the "
            "cursor round-trip; check the fixture and the endpoint"
        )
        first = (
            await async_test_client.get(
                "/api/v1/settings/observability/sinks?limit=1",
                headers=auth_headers,
            )
        ).json()
        assert len(first["data"]) == 1
        collected = list(first["data"])
        cursor = first["pagination"]["next_cursor"]
        assert cursor is not None
        while cursor:
            page = (
                await async_test_client.get(
                    f"/api/v1/settings/observability/sinks?limit=1&cursor={cursor}",
                    headers=auth_headers,
                )
            ).json()
            collected.extend(page["data"])
            cursor = page["pagination"]["next_cursor"]
        assert collected == full

    async def test_tampered_cursor_rejected(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks?cursor=garbage",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_list_sinks_with_console_level_override(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Override the console sink level and verify list_sinks reflects it."""
        overrides = json.dumps({"__console__": {"level": "error"}})
        put_resp = await async_test_client.put(
            "/api/v1/settings/observability/sink_overrides",
            json={"value": overrides},
            headers=auth_headers,
        )
        assert put_resp.status_code == 200

        resp = await async_test_client.get(
            "/api/v1/settings/observability/sinks",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        sinks = body["data"]

        console = next(s for s in sinks if s["identifier"] == CONSOLE_SINK_ID)
        assert console["level"] == "ERROR"
        assert console["enabled"] is True
        assert console["is_default"] is True


@pytest.mark.unit
class TestTestSinkConfig:
    """Tests for POST /settings/observability/sinks/_test."""

    async def test_valid_empty_config(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "{}", "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["valid"] is True
        assert body["data"]["error"] is None

    async def test_valid_override(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        overrides = json.dumps(
            {
                "__console__": {"level": "warning"},
            }
        )
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": overrides, "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is True

    async def test_valid_custom_sink(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        custom = json.dumps(
            [
                {
                    "file_path": "custom.log",
                    "level": "info",
                }
            ]
        )
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "{}", "custom_sinks": custom},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is True

    async def test_invalid_json_returns_error(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "not-json", "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is False
        assert body["data"]["error"] is not None
        assert "Invalid JSON" in body["data"]["error"]

    async def test_invalid_sink_identifier_returns_error(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        overrides = json.dumps(
            {
                "nonexistent_sink": {"level": "info"},
            }
        )
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": overrides, "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is False
        assert "Unknown sink identifier" in body["data"]["error"]

    async def test_disable_console_returns_error(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        overrides = json.dumps(
            {
                "__console__": {"enabled": False},
            }
        )
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": overrides, "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is False
        assert "console" in body["data"]["error"].lower()

    async def test_invalid_level_returns_error(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        overrides = json.dumps(
            {
                "__console__": {"level": "INVALID_LEVEL"},
            }
        )
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": overrides, "custom_sinks": "[]"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is False
        assert "Invalid level" in body["data"]["error"]

    async def test_custom_sink_missing_path_returns_error(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        custom = json.dumps([{"level": "info"}])
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "{}", "custom_sinks": custom},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is False
        assert "file_path" in body["data"]["error"]

    async def test_observer_cannot_test_config(
        self,
        async_test_client: LoopAsyncClient,
        observer_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "{}", "custom_sinks": "[]"},
            headers=observer_headers,
        )
        assert resp.status_code == 403

    async def test_manager_can_test_config(
        self,
        async_test_client: LoopAsyncClient,
        manager_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={"sink_overrides": "{}", "custom_sinks": "[]"},
            headers=manager_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is True

    async def test_defaults_used_when_fields_omitted(
        self,
        async_test_client: LoopAsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/settings/observability/sinks/_test",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["valid"] is True
