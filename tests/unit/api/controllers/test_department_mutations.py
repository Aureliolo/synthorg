"""Tests for department mutation endpoints (POST, PATCH, DELETE departments)."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestCreateDepartment:
    async def test_create_department_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/departments",
            json={"name": "engineering"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "engineering"

    async def test_create_department_duplicate_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "engineering"},
        )
        resp = await async_test_client.post(
            "/api/v1/departments",
            json={"name": "engineering"},
        )
        assert resp.status_code == 409

    async def test_create_department_observer_denied(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        async_test_client.headers.update(make_auth_headers("observer"))
        resp = await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestUpdateDepartment:
    async def test_update_department_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/eng",
            json={"budget_percent": 40.0},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["budget_percent"] == 40.0

    async def test_update_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/departments/nonexistent",
            json={"budget_percent": 10.0},
        )
        assert resp.status_code == 404

    async def test_update_department_stale_etag(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        resp = await async_test_client.patch(
            "/api/v1/departments/eng",
            json={"budget_percent": 50.0},
            headers={"If-Match": '"stale-etag-value000"'},
        )
        assert resp.status_code == 409

    async def test_update_department_matching_etag(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        # First update to get an ETag in the response
        resp1 = await async_test_client.patch(
            "/api/v1/departments/eng",
            json={"budget_percent": 30.0},
        )
        assert resp1.status_code == 200
        etag = resp1.headers.get("etag")
        assert etag is not None

        # Use the returned ETag for a second update
        resp2 = await async_test_client.patch(
            "/api/v1/departments/eng",
            json={"budget_percent": 40.0},
            headers={"If-Match": etag},
        )
        assert resp2.status_code == 200

    async def test_update_department_no_etag(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        # No If-Match header -- should succeed
        resp = await async_test_client.patch(
            "/api/v1/departments/eng",
            json={"budget_percent": 60.0},
        )
        assert resp.status_code == 200


@pytest.mark.unit
class TestDeleteDepartment:
    async def test_delete_department_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        resp = await async_test_client.delete("/api/v1/departments/eng")
        assert resp.status_code == 204

    async def test_delete_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.delete("/api/v1/departments/nonexistent")
        assert resp.status_code == 404

    async def test_delete_department_with_agents_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "developer",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = await async_test_client.delete("/api/v1/departments/eng")
        assert resp.status_code == 409


@pytest.mark.unit
class TestReorderAgents:
    async def test_reorder_agents_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "bob",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = await async_test_client.post(
            "/api/v1/departments/eng/reorder-agents",
            json={"agent_names": ["bob", "alice"]},
        )
        assert resp.status_code == 201
        names = [a["name"] for a in resp.json()["data"]]
        assert names == ["bob", "alice"]
