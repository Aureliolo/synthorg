"""Tests for company mutation endpoints."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestUpdateCompany:
    async def test_patch_company_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/company",
            json={"company_name": "New Name"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["company_name"] == "New Name"

    async def test_patch_company_observer_denied(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/company",
            json={"company_name": "New Name"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestReorderDepartments:
    async def test_reorder_two_departments(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Create two departments and reorder them
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "alpha"},
        )
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "beta"},
        )
        resp = await async_test_client.post(
            "/api/v1/company/reorder-departments",
            json={"department_names": ["beta", "alpha"]},
        )
        assert resp.status_code == 200
        names = [d["name"] for d in resp.json()["data"]]
        assert names == ["beta", "alpha"]

    async def test_reorder_observer_denied(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/company/reorder-departments",
            json={"department_names": []},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403
