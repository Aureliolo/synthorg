"""Tests for company controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002

from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestCompanyController:
    def test_get_company(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/company", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["company_name"] == "test-company"

    def test_list_departments(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/company/departments", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

    def test_company_requires_read_access(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/company",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
